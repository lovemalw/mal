"""Core .env parser with variable interpolation and type casting.

Supports:
- Standard KEY=VALUE syntax
- Quoted values (single, double, backtick)
- Multiline values with double quotes
- Variable interpolation: ${VAR} and $VAR
- Default values: ${VAR:-default}
- Comments (# inline and standalone)
- Export prefix: export KEY=VALUE
- Type casting via get()
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Type, TypeVar, Union, overload

from ._types import CastFn, EnvDict

T = TypeVar("T")

_INTERPOLATION_RE = re.compile(
    r"\$\{([^}:]+)(?::-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)
_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:export\s+)?          # optional export prefix
    ([A-Za-z_][A-Za-z0-9_.]*)  # key
    \s*=\s*                 # separator
    (.*)                    # value (parsed further)
    $
    """,
    re.VERBOSE,
)


class EnvCore:
    """High-performance .env file parser.

    Args:
        path: Path to .env file (default: ".env" in cwd).
        override: If True, override existing os.environ values.
        interpolate: If True, resolve ${VAR} references.
        encoding: File encoding (default: utf-8).
    """

    __slots__ = ("_path", "_override", "_interpolate", "_encoding", "_cache")

    def __init__(
        self,
        path: Union[str, Path] = ".env",
        *,
        override: bool = False,
        interpolate: bool = True,
        encoding: str = "utf-8",
    ) -> None:
        self._path = Path(path)
        self._override = override
        self._interpolate = interpolate
        self._encoding = encoding
        self._cache: Optional[EnvDict] = None

    def load(self) -> EnvDict:
        """Parse the .env file and inject into os.environ.

        Returns:
            Dict of all parsed key-value pairs.
        """
        if self._cache is not None:
            return self._cache

        raw = self._read()
        parsed = _parse_content(raw)

        if self._interpolate:
            parsed = _resolve_interpolation(parsed)

        for key, value in parsed.items():
            if self._override or key not in os.environ:
                os.environ[key] = value

        self._cache = parsed
        return parsed

    def reload(self) -> EnvDict:
        """Force re-read and re-parse the file."""
        self._cache = None
        return self.load()

    @overload
    def get(self, key: str) -> Optional[str]: ...
    @overload
    def get(self, key: str, *, default: str) -> str: ...
    @overload
    def get(self, key: str, *, cast: Type[T]) -> Optional[T]: ...
    @overload
    def get(self, key: str, *, cast: Type[T], default: T) -> T: ...

    def get(
        self,
        key: str,
        *,
        cast: Optional[CastFn] = None,
        default: Any = None,
    ) -> Any:
        """Get a value with optional type casting.

        Args:
            key: Environment variable name.
            cast: Callable to cast the string value (int, float, bool, etc.).
            default: Default if key is missing.

        Returns:
            The (optionally cast) value, or default.
        """
        if self._cache is None:
            self.load()

        raw = self._cache.get(key) if self._cache else None
        if raw is None:
            raw = os.environ.get(key)

        if raw is None:
            return default

        if cast is None:
            return raw

        if cast is bool:
            return raw.lower() in ("1", "true", "yes", "on")

        return cast(raw)

    def keys(self) -> Sequence[str]:
        """Return all parsed keys."""
        if self._cache is None:
            self.load()
        return list(self._cache.keys()) if self._cache else []

    def as_dict(self) -> EnvDict:
        """Return a copy of the parsed environment dict."""
        if self._cache is None:
            self.load()
        return dict(self._cache) if self._cache else {}

    def _read(self) -> str:
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding=self._encoding)


def load(
    path: Union[str, Path] = ".env",
    *,
    override: bool = False,
    interpolate: bool = True,
    encoding: str = "utf-8",
) -> EnvDict:
    """Load a .env file into os.environ and return parsed dict.

    Convenience wrapper around EnvCore.

    Args:
        path: Path to .env file.
        override: Override existing env vars.
        interpolate: Resolve ${VAR} references.
        encoding: File encoding.

    Returns:
        Dict of parsed key-value pairs.
    """
    core = EnvCore(path, override=override, interpolate=interpolate, encoding=encoding)
    return core.load()


def loads(content: str, *, interpolate: bool = True) -> EnvDict:
    """Parse .env content from a string (does NOT modify os.environ).

    Args:
        content: Raw .env file content.
        interpolate: Resolve ${VAR} references.

    Returns:
        Dict of parsed key-value pairs.
    """
    parsed = _parse_content(content)
    if interpolate:
        parsed = _resolve_interpolation(parsed)
    return parsed


def get(
    key: str,
    *,
    cast: Optional[CastFn] = None,
    default: Any = None,
) -> Any:
    """Get an environment variable with optional casting.

    Does NOT require prior load() — reads directly from os.environ.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    if cast is None:
        return raw
    if cast is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    return cast(raw)


def _parse_content(content: str) -> EnvDict:
    """Parse raw .env content into a dict."""
    result: EnvDict = {}
    lines = content.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line or line.startswith("#"):
            continue

        m = _LINE_RE.match(line)
        if not m:
            continue

        key = m.group(1)
        raw_value = m.group(2).strip()

        value, consumed = _parse_value(raw_value, lines, i - 1)
        i += consumed

        result[key] = value

    return result


def _parse_value(raw: str, lines: list, current_line: int) -> tuple:
    """Parse a value, handling quotes and multiline."""
    extra_consumed = 0

    if not raw:
        return "", 0

    if raw[0] in ('"', "'", "`"):
        quote = raw[0]
        if raw.endswith(quote) and len(raw) > 1:
            return _unescape(raw[1:-1], quote), 0
        parts = [raw[1:]]
        idx = current_line + 1
        while idx < len(lines):
            line = lines[idx]
            extra_consumed += 1
            if line.rstrip().endswith(quote):
                parts.append(line.rstrip()[:-1])
                break
            parts.append(line)
            idx += 1
        return _unescape("\n".join(parts), quote), extra_consumed

    comment_idx = _find_inline_comment(raw)
    if comment_idx >= 0:
        raw = raw[:comment_idx].rstrip()

    return raw, 0


def _find_inline_comment(value: str) -> int:
    """Find the position of an inline # comment (not inside quotes)."""
    in_quote = None
    for i, ch in enumerate(value):
        if ch in ('"', "'") and in_quote is None:
            in_quote = ch
        elif ch == in_quote:
            in_quote = None
        elif ch == "#" and in_quote is None and i > 0 and value[i - 1] == " ":
            return i
    return -1


def _unescape(value: str, quote: str) -> str:
    """Handle escape sequences in quoted values."""
    if quote == "'":
        return value
    return (
        value.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _resolve_interpolation(env: EnvDict) -> EnvDict:
    """Resolve ${VAR}, $VAR, and ${VAR:-default} references."""
    resolved: EnvDict = {}

    for key in env:
        resolved[key] = _resolve_value(env[key], env, resolved)

    return resolved


def _resolve_value(
    value: str, original: EnvDict, resolved: EnvDict, _depth: int = 0
) -> str:
    """Recursively resolve a single value's references."""
    if _depth > 10:
        return value

    def replacer(m: re.Match) -> str:
        ref_key = m.group(1) or m.group(3)
        default = m.group(2)

        if ref_key in resolved:
            return resolved[ref_key]
        if ref_key in original:
            return _resolve_value(original[ref_key], original, resolved, _depth + 1)
        env_val = os.environ.get(ref_key)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        return m.group(0)

    return _INTERPOLATION_RE.sub(replacer, value)
