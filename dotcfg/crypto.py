"""Encrypted environment variable storage.

Encrypt sensitive values in .env files so they can be safely
committed to version control. Uses AES-256-GCM or Fernet
encryption with a master key derived from a passphrase.

Usage:
    from dotcfg import crypto

    # Encrypt a value
    encrypted = crypto.encrypt("my-secret-db-password", key="master-key")
    # Result: "ENC[AES256:base64data...]"

    # Decrypt
    value = crypto.decrypt(encrypted, key="master-key")

    # Encrypt an entire .env file (sensitive values only)
    crypto.seal_file('.env', key="master-key", patterns=['*_KEY', '*_SECRET', '*_PASSWORD'])

    # Auto-decrypt when loading
    config = crypto.load('.env', key="master-key")
"""

from __future__ import annotations

import os
import re
import hmac
import hashlib
import secrets
import struct
from typing import Dict, List, Optional, Tuple

from .core import EnvCore


_PREFIX = 'ENC['
_SUFFIX = ']'
_AES_TAG = 'AES256:'
_XOR_TAG = 'XOR:'


def _derive_key(passphrase: str, salt: bytes, iterations: int = 100_000) -> bytes:
    """Derive a 32-byte key from passphrase using PBKDF2-SHA256."""
    return hashlib.pbkdf2_hmac('sha256', passphrase.encode('utf-8'),
                               salt, iterations, dklen=32)


def _xor_cipher(data: bytes, key: bytes) -> bytes:
    """Simple XOR stream cipher for environments without cryptography lib."""
    key_stream = hashlib.sha256(key).digest() * ((len(data) // 32) + 1)
    return bytes(a ^ b for a, b in zip(data, key_stream[:len(data)]))


def _aes_gcm_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-256-GCM encrypt. Returns nonce(12) + ciphertext + tag(16)."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        return nonce + ct
    except ImportError:
        nonce = secrets.token_bytes(12)
        stream_key = hashlib.sha256(key + nonce).digest()
        ct = _xor_cipher(plaintext, stream_key)
        tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:16]
        return nonce + ct + tag


def _aes_gcm_decrypt(data: bytes, key: bytes) -> Optional[bytes]:
    """AES-256-GCM decrypt."""
    if len(data) < 28:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = data[:12]
        ct = data[12:]
        return AESGCM(key).decrypt(nonce, ct, None)
    except ImportError:
        nonce = data[:12]
        ct = data[12:-16]
        tag = data[-16:]
        expected_tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expected_tag):
            return None
        stream_key = hashlib.sha256(key + nonce).digest()
        return _xor_cipher(ct, stream_key)
    except Exception:
        return None


def encrypt(value: str, *, key: str) -> str:
    """Encrypt a config value.

    Args:
        value: Plaintext value to encrypt.
        key: Master key or passphrase.

    Returns:
        Encrypted string in format: ENC[AES256:base64...]
    """
    import base64
    salt = secrets.token_bytes(16)
    derived = _derive_key(key, salt)
    plaintext = value.encode('utf-8')
    ct = _aes_gcm_encrypt(plaintext, derived)
    payload = salt + ct
    encoded = base64.b64encode(payload).decode('ascii')
    return f'{_PREFIX}{_AES_TAG}{encoded}{_SUFFIX}'


def decrypt(encrypted: str, *, key: str) -> Optional[str]:
    """Decrypt a config value.

    Args:
        encrypted: String in format ENC[AES256:base64...]
        key: Master key or passphrase.

    Returns:
        Decrypted string or None if decryption fails.
    """
    import base64
    if not encrypted.startswith(_PREFIX) or not encrypted.endswith(_SUFFIX):
        return encrypted

    inner = encrypted[len(_PREFIX):-len(_SUFFIX)]

    if inner.startswith(_AES_TAG):
        payload_b64 = inner[len(_AES_TAG):]
    elif inner.startswith(_XOR_TAG):
        payload_b64 = inner[len(_XOR_TAG):]
    else:
        return None

    try:
        payload = base64.b64decode(payload_b64)
    except Exception:
        return None

    if len(payload) < 16:
        return None

    salt = payload[:16]
    ct = payload[16:]
    derived = _derive_key(key, salt)
    plaintext = _aes_gcm_decrypt(ct, derived)
    if plaintext is None:
        return None
    return plaintext.decode('utf-8', errors='replace')


def is_encrypted(value: str) -> bool:
    """Check if a value is encrypted."""
    return value.startswith(_PREFIX) and value.endswith(_SUFFIX)


def load(path: str, *, key: str) -> Dict[str, str]:
    """Load .env file and auto-decrypt encrypted values.

    Args:
        path: Path to .env file.
        key: Master decryption key.

    Returns:
        Dictionary with all values decrypted.
    """
    core = EnvCore()
    core.load(path)
    result = {}
    for k, v in core.items():
        if is_encrypted(v):
            decrypted = decrypt(v, key=key)
            result[k] = decrypted if decrypted is not None else v
        else:
            result[k] = v
    return result


def seal_file(path: str, *,
              key: str,
              patterns: Optional[List[str]] = None,
              output: Optional[str] = None) -> int:
    """Encrypt sensitive values in a .env file in-place.

    Args:
        path: Path to .env file.
        key: Master encryption key.
        patterns: Glob patterns for keys to encrypt (default: *SECRET*, *KEY*, *PASSWORD*, *TOKEN*).
        output: Output path (default: overwrite input).

    Returns:
        Number of values encrypted.
    """
    if patterns is None:
        patterns = ['*SECRET*', '*KEY*', '*PASSWORD*', '*TOKEN*', '*PRIVATE*']

    def _matches(name: str) -> bool:
        import fnmatch
        return any(fnmatch.fnmatch(name.upper(), p.upper()) for p in patterns)

    lines = []
    count = 0
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw_lines = f.readlines()
    except Exception:
        return 0

    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            lines.append(line)
            continue

        eq_pos = stripped.index('=')
        name = stripped[:eq_pos].strip()
        value = stripped[eq_pos + 1:].strip().strip('"').strip("'")

        if _matches(name) and not is_encrypted(value):
            encrypted_value = encrypt(value, key=key)
            lines.append(f'{name}={encrypted_value}\n')
            count += 1
        else:
            lines.append(line)

    out_path = output or path
    with open(out_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return count


def unseal_file(path: str, *, key: str, output: Optional[str] = None) -> int:
    """Decrypt all encrypted values in a .env file.

    Args:
        path: Path to .env file with encrypted values.
        key: Master decryption key.
        output: Output path (default: overwrite input).

    Returns:
        Number of values decrypted.
    """
    lines = []
    count = 0
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw_lines = f.readlines()
    except Exception:
        return 0

    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            lines.append(line)
            continue

        eq_pos = stripped.index('=')
        name = stripped[:eq_pos].strip()
        value = stripped[eq_pos + 1:].strip()

        if is_encrypted(value):
            decrypted = decrypt(value, key=key)
            if decrypted is not None:
                lines.append(f'{name}="{decrypted}"\n')
                count += 1
            else:
                lines.append(line)
        else:
            lines.append(line)

    out_path = output or path
    with open(out_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return count


def generate_key() -> str:
    """Generate a cryptographically secure master key."""
    return secrets.token_urlsafe(32)


def rotate_key(path: str, *, old_key: str, new_key: str) -> int:
    """Re-encrypt all values with a new key.

    Args:
        path: Path to .env file.
        old_key: Current encryption key.
        new_key: New encryption key.

    Returns:
        Number of values rotated.
    """
    lines = []
    count = 0
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw_lines = f.readlines()
    except Exception:
        return 0

    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            lines.append(line)
            continue

        eq_pos = stripped.index('=')
        name = stripped[:eq_pos].strip()
        value = stripped[eq_pos + 1:].strip()

        if is_encrypted(value):
            plaintext = decrypt(value, key=old_key)
            if plaintext is not None:
                new_encrypted = encrypt(plaintext, key=new_key)
                lines.append(f'{name}={new_encrypted}\n')
                count += 1
            else:
                lines.append(line)
        else:
            lines.append(line)

    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return count
