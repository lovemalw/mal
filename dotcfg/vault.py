"""Secret masking and sanitization utilities.

Prevents accidental leakage of sensitive environment values in logs,
error messages, and debug output.

Usage::

    from dotcfg.vault import SecretVault

    vault = SecretVault(mask_keys=["*_KEY", "*_SECRET", "*_TOKEN", "DATABASE_URL"])
    safe = vault.mask_dict(os.environ)
    print(safe["AWS_SECRET_ACCESS_KEY"])  # "aws****key"

    # Or mask a log message:
    vault.scrub("Connection to postgres://user:pass@host/db failed")
    # -> "Connection to postgres://****:****@host/db failed"
"""

from __future__ import annotations

import fnmatch
import re
from typing import Dict, List, Optional, Sequence

__all__ = ["SecretVault", "mask_value", "scrub_urls"]

_DEFAULT_PATTERNS = [
    "*_KEY",
    "*_SECRET",
    "*_TOKEN",
    "*_PASSWORD",
    "*_PASS",
    "*_CREDENTIAL*",
    "*_API_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "AMQP_URL",
    "SECRET*",
    "AUTH*",
]

_URL_CRED_RE = re.compile(r"(://[^:]+:)([^@]+)(@)")


def mask_value(value: str, *, visible_chars: int = 3) -> str:
    """Mask a sensitive value, keeping first and last N visible chars."""
    if len(value) <= visible_chars * 2 + 4:
        return "********"
    start = value[:visible_chars]
    end = value[-visible_chars:]
    return f"{start}****{end}"


def scrub_urls(text: str) -> str:
    """Remove credentials from URLs in a text string."""
    return _URL_CRED_RE.sub(r"\1****\3", text)


class SecretVault:
    """Manages identification and masking of sensitive env vars.

    Args:
        mask_keys: Glob patterns for keys to consider sensitive.
        visible_chars: Number of chars to show at start/end of masked values.
    """

    def __init__(
        self,
        mask_keys: Optional[Sequence[str]] = None,
        *,
        visible_chars: int = 3,
    ) -> None:
        self._patterns = list(mask_keys) if mask_keys else list(_DEFAULT_PATTERNS)
        self._visible = visible_chars

    def is_sensitive(self, key: str) -> bool:
        """Check if a key matches any sensitive pattern."""
        upper = key.upper()
        return any(fnmatch.fnmatch(upper, p.upper()) for p in self._patterns)

    def mask(self, key: str, value: str) -> str:
        """Mask a value if its key is sensitive."""
        if self.is_sensitive(key):
            return mask_value(value, visible_chars=self._visible)
        return value

    def mask_dict(self, env: Dict[str, str]) -> Dict[str, str]:
        """Return a copy of the dict with sensitive values masked."""
        return {k: self.mask(k, v) for k, v in env.items()}

    def scrub(self, text: str) -> str:
        """Scrub sensitive data from a text string."""
        return scrub_urls(text)

    def add_pattern(self, pattern: str) -> None:
        """Add a new sensitive key pattern."""
        self._patterns.append(pattern)

    @property
    def patterns(self) -> List[str]:
        """Current list of sensitive patterns."""
        return list(self._patterns)
