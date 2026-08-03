"""Configuration health checks and validation dashboard.

Verify that all required environment variables are set,
have valid values, and meet type/format constraints.

Usage:
    from dotcfg import health

    # Quick check
    report = health.check({
        'DATABASE_URL': health.required(format='url'),
        'PORT': health.required(type=int, range=(1, 65535)),
        'DEBUG': health.optional(type=bool, default='false'),
        'API_KEY': health.required(min_length=32),
    })

    if not report.healthy:
        print(report.summary())
        sys.exit(1)

    # Or use decorator
    @health.require('DATABASE_URL', 'REDIS_URL', 'SECRET_KEY')
    def main():
        ...
"""

from __future__ import annotations

import os
import re
import sys
import functools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Type


@dataclass
class VarSpec:
    """Specification for an environment variable."""
    name: str = ''
    required: bool = True
    type: Optional[Type] = None
    default: Optional[str] = None
    format: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    choices: Optional[List[str]] = None
    range: Optional[Tuple[Any, Any]] = None
    pattern: Optional[str] = None
    description: str = ''


@dataclass
class CheckResult:
    """Result of checking a single variable."""
    name: str
    status: str  # 'ok', 'missing', 'invalid', 'warning'
    value: Optional[str] = None
    message: str = ''
    masked_value: str = ''


@dataclass
class HealthReport:
    """Overall health report."""
    results: List[CheckResult] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(r.status in ('ok', 'warning') for r in self.results)

    @property
    def errors(self) -> List[CheckResult]:
        return [r for r in self.results if r.status in ('missing', 'invalid')]

    @property
    def warnings(self) -> List[CheckResult]:
        return [r for r in self.results if r.status == 'warning']

    def summary(self, show_values: bool = False) -> str:
        lines = []
        total = len(self.results)
        ok = sum(1 for r in self.results if r.status == 'ok')
        lines.append(f'Config Health: {ok}/{total} OK')
        lines.append('')

        for r in self.results:
            icon = {'ok': '[OK]', 'missing': '[!!]',
                    'invalid': '[XX]', 'warning': '[~~]'}.get(r.status, '[??]')
            val_str = ''
            if show_values and r.masked_value:
                val_str = f' = {r.masked_value}'
            msg = f'  ({r.message})' if r.message else ''
            lines.append(f'  {icon} {r.name}{val_str}{msg}')

        if not self.healthy:
            lines.append('')
            lines.append(f'UNHEALTHY: {len(self.errors)} error(s)')
        return '\n'.join(lines)

    def __bool__(self) -> bool:
        return self.healthy


def required(type: Optional[Type] = None, **kwargs) -> VarSpec:
    """Define a required variable spec."""
    return VarSpec(required=True, type=type, **kwargs)


def optional(type: Optional[Type] = None, **kwargs) -> VarSpec:
    """Define an optional variable spec."""
    return VarSpec(required=False, type=type, **kwargs)


_FORMAT_VALIDATORS = {
    'url': re.compile(r'^https?://\S+$'),
    'email': re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    'ipv4': re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'),
    'uuid': re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I),
    'hex': re.compile(r'^[0-9a-fA-F]+$'),
    'base64': re.compile(r'^[A-Za-z0-9+/]*={0,2}$'),
    'port': re.compile(r'^\d{1,5}$'),
    'path': re.compile(r'^[/\\]?\S+$'),
}


def _mask_value(value: str) -> str:
    """Mask sensitive value for display."""
    if len(value) <= 4:
        return '****'
    return value[:2] + '*' * (len(value) - 4) + value[-2:]


def _check_var(name: str, spec: VarSpec) -> CheckResult:
    """Check a single environment variable."""
    value = os.environ.get(name)

    if value is None or value == '':
        if spec.required:
            return CheckResult(name=name, status='missing',
                               message='Required variable not set')
        elif spec.default is not None:
            return CheckResult(name=name, status='ok', value=spec.default,
                               masked_value=f'(default: {spec.default})',
                               message='Using default')
        else:
            return CheckResult(name=name, status='ok', value=None,
                               message='Optional, not set')

    masked = _mask_value(value)

    if spec.type is not None:
        try:
            if spec.type == bool:
                if value.lower() not in ('true', 'false', '1', '0', 'yes', 'no'):
                    return CheckResult(name=name, status='invalid', value=value,
                                       masked_value=masked,
                                       message=f'Cannot cast to {spec.type.__name__}')
            else:
                spec.type(value)
        except (ValueError, TypeError):
            return CheckResult(name=name, status='invalid', value=value,
                               masked_value=masked,
                               message=f'Cannot cast to {spec.type.__name__}')

    if spec.format and spec.format in _FORMAT_VALIDATORS:
        if not _FORMAT_VALIDATORS[spec.format].match(value):
            return CheckResult(name=name, status='invalid', value=value,
                               masked_value=masked,
                               message=f'Does not match format: {spec.format}')

    if spec.min_length and len(value) < spec.min_length:
        return CheckResult(name=name, status='invalid', value=value,
                           masked_value=masked,
                           message=f'Too short (min {spec.min_length})')

    if spec.max_length and len(value) > spec.max_length:
        return CheckResult(name=name, status='invalid', value=value,
                           masked_value=masked,
                           message=f'Too long (max {spec.max_length})')

    if spec.choices and value not in spec.choices:
        return CheckResult(name=name, status='invalid', value=value,
                           masked_value=masked,
                           message=f'Not in allowed values: {spec.choices}')

    if spec.range:
        try:
            num = float(value)
            if num < spec.range[0] or num > spec.range[1]:
                return CheckResult(name=name, status='invalid', value=value,
                                   masked_value=masked,
                                   message=f'Out of range [{spec.range[0]}, {spec.range[1]}]')
        except ValueError:
            pass

    if spec.pattern:
        if not re.match(spec.pattern, value):
            return CheckResult(name=name, status='invalid', value=value,
                               masked_value=masked,
                               message=f'Does not match pattern: {spec.pattern}')

    return CheckResult(name=name, status='ok', value=value, masked_value=masked)


def check(specs: Dict[str, VarSpec],
          source: Optional[Dict[str, str]] = None) -> HealthReport:
    """Run health checks on environment variables.

    Args:
        specs: Dictionary of {var_name: VarSpec}.
        source: Optional dict to check instead of os.environ.

    Returns:
        HealthReport with all results.
    """
    old_env = None
    if source is not None:
        old_env = os.environ.copy()
        os.environ.update(source)

    results = []
    for name, spec in specs.items():
        spec.name = name
        results.append(_check_var(name, spec))

    if old_env is not None:
        os.environ.clear()
        os.environ.update(old_env)

    return HealthReport(results=results)


def require(*var_names: str):
    """Decorator that checks required env vars before running a function.

    Usage:
        @health.require('DATABASE_URL', 'API_KEY')
        def main():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            missing = [v for v in var_names if not os.environ.get(v)]
            if missing:
                print(f'[dotcfg] Missing required env vars: {", ".join(missing)}',
                      file=sys.stderr)
                sys.exit(1)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def audit(source: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Quick audit of all environment variables.

    Returns statistics about the current environment.
    """
    env = source or dict(os.environ)
    sensitive_patterns = ['KEY', 'SECRET', 'PASSWORD', 'TOKEN', 'PRIVATE', 'CREDENTIAL']

    total = len(env)
    sensitive = [k for k in env if any(p in k.upper() for p in sensitive_patterns)]
    empty = [k for k in env if env[k] == '']

    return {
        'total_vars': total,
        'sensitive_count': len(sensitive),
        'sensitive_vars': sensitive,
        'empty_count': len(empty),
        'empty_vars': empty,
        'has_dotenv': os.path.isfile('.env'),
        'has_local': os.path.isfile('.env.local'),
    }
