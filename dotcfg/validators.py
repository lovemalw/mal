"""Built-in validators for environment variable values.

Usage::

    from dotcfg import load
    from dotcfg.validators import Url, Port, Email, OneOf, Range, Regex

    env = load()
    Url().validate("DATABASE_URL", env.get("DATABASE_URL"))
    Port().validate("PORT", env.get("PORT"))
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence, Union

__all__ = [
    "ValidationError",
    "Validator",
    "Required",
    "Url",
    "Port",
    "Email",
    "OneOf",
    "Range",
    "Regex",
    "Boolean",
    "IPv4",
    "MinLength",
    "MaxLength",
    "Json",
]


class ValidationError(ValueError):
    """Raised when an environment variable fails validation."""

    def __init__(self, key: str, value: Any, message: str) -> None:
        self.key = key
        self.value = value
        self.message = message
        super().__init__(f"{key}: {message}")


class Validator:
    """Base class for validators."""

    def validate(self, key: str, value: Optional[str]) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


class Required(Validator):
    """Ensures the variable exists and is non-empty."""

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None or value.strip() == "":
            raise ValidationError(key, value, "is required but missing or empty")
        return value


class Url(Validator):
    """Validates URL format with allowed schemes."""

    _PATTERN = re.compile(
        r"^(https?|ftp|postgresql|postgres|mysql|redis|amqp|mongodb|sqlite)"
        r"://[^\s/$.?#].[^\s]*$",
        re.IGNORECASE,
    )

    def __init__(self, schemes: Optional[Sequence[str]] = None) -> None:
        self.schemes = schemes

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "URL is required")
        if not self._PATTERN.match(value):
            raise ValidationError(key, value, f"invalid URL format: {value!r}")
        if self.schemes:
            scheme = value.split("://")[0].lower()
            if scheme not in self.schemes:
                raise ValidationError(
                    key, value, f"scheme {scheme!r} not in {self.schemes}"
                )
        return value


class Port(Validator):
    """Validates a network port number (1-65535)."""

    def __init__(self, min_port: int = 1, max_port: int = 65535) -> None:
        self.min_port = min_port
        self.max_port = max_port

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "port is required")
        try:
            port = int(value)
        except (ValueError, TypeError):
            raise ValidationError(key, value, f"not a valid integer: {value!r}")
        if not (self.min_port <= port <= self.max_port):
            raise ValidationError(
                key, value, f"port {port} not in range [{self.min_port}, {self.max_port}]"
            )
        return value


class Email(Validator):
    """Basic email format validation."""

    _PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "email is required")
        if not self._PATTERN.match(value):
            raise ValidationError(key, value, f"invalid email format: {value!r}")
        return value


class OneOf(Validator):
    """Value must be one of the given choices."""

    def __init__(self, choices: Sequence[str], case_sensitive: bool = True) -> None:
        self.choices = list(choices)
        self.case_sensitive = case_sensitive

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, f"must be one of {self.choices}")
        check = value if self.case_sensitive else value.lower()
        pool = self.choices if self.case_sensitive else [c.lower() for c in self.choices]
        if check not in pool:
            raise ValidationError(key, value, f"{value!r} not in {self.choices}")
        return value

    def __repr__(self) -> str:
        return f"OneOf({self.choices!r})"


class Range(Validator):
    """Numeric value within a range."""

    def __init__(
        self,
        min_val: Optional[Union[int, float]] = None,
        max_val: Optional[Union[int, float]] = None,
    ) -> None:
        self.min_val = min_val
        self.max_val = max_val

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "numeric value required")
        try:
            num = float(value)
        except (ValueError, TypeError):
            raise ValidationError(key, value, f"not numeric: {value!r}")
        if self.min_val is not None and num < self.min_val:
            raise ValidationError(key, value, f"{num} < minimum {self.min_val}")
        if self.max_val is not None and num > self.max_val:
            raise ValidationError(key, value, f"{num} > maximum {self.max_val}")
        return value


class Regex(Validator):
    """Value must match a regex pattern."""

    def __init__(self, pattern: str, flags: int = 0, description: str = "") -> None:
        self.pattern = re.compile(pattern, flags)
        self.description = description or f"match /{pattern}/"

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "value required")
        if not self.pattern.match(value):
            raise ValidationError(key, value, f"does not {self.description}")
        return value


class Boolean(Validator):
    """Validates a boolean-like value."""

    _TRUE = {"1", "true", "yes", "on", "enabled"}
    _FALSE = {"0", "false", "no", "off", "disabled", ""}

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "boolean value required")
        if value.lower() not in (self._TRUE | self._FALSE):
            raise ValidationError(
                key, value, f"{value!r} is not a recognized boolean value"
            )
        return value


class IPv4(Validator):
    """Validates an IPv4 address."""

    _PATTERN = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "IPv4 address required")
        m = self._PATTERN.match(value)
        if not m or not all(0 <= int(g) <= 255 for g in m.groups()):
            raise ValidationError(key, value, f"invalid IPv4: {value!r}")
        return value


class MinLength(Validator):
    """Value must be at least N characters."""

    def __init__(self, length: int) -> None:
        self.length = length

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None or len(value) < self.length:
            raise ValidationError(
                key, value, f"must be at least {self.length} characters"
            )
        return value


class MaxLength(Validator):
    """Value must be at most N characters."""

    def __init__(self, length: int) -> None:
        self.length = length

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is not None and len(value) > self.length:
            raise ValidationError(
                key, value, f"must be at most {self.length} characters"
            )
        return value or ""


class Json(Validator):
    """Validates that the value is valid JSON."""

    def validate(self, key: str, value: Optional[str]) -> str:
        if value is None:
            raise ValidationError(key, value, "JSON value required")
        import json

        try:
            json.loads(value)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValidationError(key, value, f"invalid JSON: {e}")
        return value
