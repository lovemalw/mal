"""Declarative environment schema definitions.

Define expected environment variables with types, defaults, validation,
and documentation — then validate all at once.

Usage::

    from dotcfg.schema import EnvSchema, Var
    from dotcfg.validators import Url, Port, OneOf

    schema = EnvSchema(
        Var("DATABASE_URL", validators=[Url()], required=True),
        Var("PORT", cast=int, default="8080", validators=[Port()]),
        Var("LOG_LEVEL", default="info", validators=[OneOf(["debug", "info", "warning", "error"])]),
        Var("DEBUG", cast=bool, default="false"),
        Var("SECRET_KEY", required=True, sensitive=True),
    )

    # Validate all at once — raises SchemaError with all failures
    config = schema.validate()

    # Access typed values
    config.DATABASE_URL  # str
    config.PORT          # int (8080)
    config.DEBUG         # bool (False)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Type

from .validators import ValidationError, Validator

__all__ = ["Var", "EnvSchema", "SchemaError", "Config"]


class SchemaError(Exception):
    """Raised when environment validation fails."""

    def __init__(self, errors: List[ValidationError]) -> None:
        self.errors = errors
        messages = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Environment validation failed ({len(errors)} errors):\n{messages}")


@dataclass
class Var:
    """Defines a single environment variable expectation.

    Args:
        name: The environment variable name.
        cast: Type to cast to (int, float, bool, str, or callable).
        default: Default value (as string) if not present.
        required: If True, variable must exist (and be non-empty).
        validators: List of Validator instances to apply.
        sensitive: If True, value is redacted in error messages and repr.
        description: Human-readable description of this variable.
    """

    name: str
    cast: Optional[Callable[[str], Any]] = None
    default: Optional[str] = None
    required: bool = False
    validators: List[Validator] = field(default_factory=list)
    sensitive: bool = False
    description: str = ""


class Config:
    """Immutable config object returned by EnvSchema.validate().

    Attributes are the validated (and cast) environment values.
    Supports both attribute access and dict-like access.
    """

    def __init__(self, values: Dict[str, Any], sensitive_keys: set) -> None:
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_sensitive", sensitive_keys)

    def __getattr__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        raise AttributeError(f"Config has no variable {name!r}")

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __contains__(self, key: str) -> bool:
        return key in self._values

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Config is immutable")

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def keys(self) -> List[str]:
        return list(self._values.keys())

    def as_dict(self, *, mask_sensitive: bool = True) -> Dict[str, Any]:
        """Return config as dict, optionally masking sensitive values."""
        if not mask_sensitive:
            return dict(self._values)
        result = {}
        for k, v in self._values.items():
            if k in self._sensitive:
                result[k] = "********"
            else:
                result[k] = v
        return result

    def __repr__(self) -> str:
        pairs = []
        for k, v in self._values.items():
            if k in self._sensitive:
                pairs.append(f"{k}=********")
            else:
                pairs.append(f"{k}={v!r}")
        return f"Config({', '.join(pairs)})"


class EnvSchema:
    """Declarative schema for environment validation.

    Validates all variables at once and returns an immutable Config object.
    """

    def __init__(self, *variables: Var) -> None:
        self.variables = list(variables)

    def validate(
        self,
        env: Optional[Dict[str, str]] = None,
        *,
        raise_on_error: bool = True,
    ) -> Config:
        """Validate environment against the schema.

        Args:
            env: Override dict (default: os.environ).
            raise_on_error: If True, raises SchemaError on failures.

        Returns:
            Config object with typed values.

        Raises:
            SchemaError: If any validation fails and raise_on_error is True.
        """
        source = env if env is not None else dict(os.environ)
        errors: List[ValidationError] = []
        values: Dict[str, Any] = {}
        sensitive_keys: set = set()

        for var in self.variables:
            raw = source.get(var.name)

            if raw is None and var.default is not None:
                raw = var.default

            if var.required and (raw is None or raw.strip() == ""):
                errors.append(
                    ValidationError(var.name, raw, "is required but missing or empty")
                )
                continue

            if raw is None:
                values[var.name] = None
                continue

            valid = True
            for validator in var.validators:
                try:
                    validator.validate(var.name, raw)
                except ValidationError as e:
                    errors.append(e)
                    valid = False
                    break

            if not valid:
                continue

            if var.cast is not None:
                try:
                    if var.cast is bool:
                        values[var.name] = raw.lower() in ("1", "true", "yes", "on")
                    else:
                        values[var.name] = var.cast(raw)
                except (ValueError, TypeError) as e:
                    errors.append(
                        ValidationError(var.name, raw, f"cast to {var.cast.__name__} failed: {e}")
                    )
                    continue
            else:
                values[var.name] = raw

            if var.sensitive:
                sensitive_keys.add(var.name)

        if errors and raise_on_error:
            raise SchemaError(errors)

        return Config(values, sensitive_keys)

    def generate_template(self, *, with_descriptions: bool = True) -> str:
        """Generate a .env.example template from the schema."""
        lines = []
        for var in self.variables:
            if with_descriptions and var.description:
                lines.append(f"# {var.description}")
            suffix = ""
            if var.required:
                suffix = "  # REQUIRED"
            elif var.default:
                suffix = f"  # default: {var.default}"
            lines.append(f"{var.name}={var.default or ''}{suffix}")
            lines.append("")
        return "\n".join(lines)

    def check(self, env: Optional[Dict[str, str]] = None) -> List[str]:
        """Return a list of human-readable issues (non-raising).

        Useful for CI checks or startup warnings.
        """
        source = env if env is not None else dict(os.environ)
        issues: List[str] = []

        for var in self.variables:
            raw = source.get(var.name)
            if raw is None and var.default is not None:
                raw = var.default
            if var.required and (raw is None or raw.strip() == ""):
                issues.append(f"MISSING: {var.name} (required)")
                continue
            if raw is None:
                continue
            for validator in var.validators:
                try:
                    validator.validate(var.name, raw)
                except ValidationError as e:
                    issues.append(f"INVALID: {e}")
                    break

        return issues
