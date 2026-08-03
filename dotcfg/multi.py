"""Multi-environment configuration layering.

Load and merge .env files based on the current environment
with a clear precedence chain. Supports .env, .env.local,
.env.{environment}, and .env.{environment}.local patterns.

Precedence (highest to lowest):
    1. .env.{env}.local  (machine-specific overrides, gitignored)
    2. .env.{env}        (environment-specific)
    3. .env.local        (local overrides, gitignored)
    4. .env              (shared defaults)

Usage:
    from dotcfg import multi

    # Auto-detect from APP_ENV / NODE_ENV / ENVIRONMENT
    config = multi.load()

    # Explicit environment
    config = multi.load(env='production')

    # Custom base directory
    config = multi.load(env='staging', path='./config/')
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

from .core import EnvCore


_ENV_DETECT_VARS = ['APP_ENV', 'NODE_ENV', 'ENVIRONMENT', 'ENV',
                    'RAILS_ENV', 'FLASK_ENV', 'DJANGO_ENV']

_DEFAULT_ENV = 'development'


def detect_environment() -> str:
    """Auto-detect current environment from system env vars.

    Checks APP_ENV, NODE_ENV, ENVIRONMENT, ENV, etc.
    Defaults to 'development' if none found.
    """
    for var in _ENV_DETECT_VARS:
        val = os.environ.get(var, '').strip().lower()
        if val:
            return val
    return _DEFAULT_ENV


def _resolve_files(base_dir: str, env: str) -> List[str]:
    """Resolve ordered list of .env files to load (lowest to highest priority)."""
    candidates = [
        '.env',
        '.env.local',
        f'.env.{env}',
        f'.env.{env}.local',
    ]
    result = []
    for name in candidates:
        path = os.path.join(base_dir, name)
        if os.path.isfile(path):
            result.append(path)
    return result


def load(env: Optional[str] = None,
         path: str = '.',
         *,
         override_system: bool = False,
         extra_files: Optional[Sequence[str]] = None) -> Dict[str, str]:
    """Load multi-environment config with layered precedence.

    Args:
        env: Environment name (auto-detected if None).
        path: Base directory to search for .env files.
        override_system: If True, loaded vars override existing env vars.
        extra_files: Additional files to load (highest priority).

    Returns:
        Merged configuration dictionary.
    """
    if env is None:
        env = detect_environment()

    base_dir = os.path.abspath(path)
    files = _resolve_files(base_dir, env)

    if extra_files:
        for f in extra_files:
            fp = os.path.abspath(f)
            if os.path.isfile(fp):
                files.append(fp)

    merged: Dict[str, str] = {}
    loaded_files: List[str] = []

    for filepath in files:
        try:
            core = EnvCore()
            core.load(filepath)
            for k, v in core.items():
                if override_system or k not in os.environ:
                    merged[k] = v
            loaded_files.append(filepath)
        except Exception:
            continue

    merged['_DOTCFG_ENV'] = env
    merged['_DOTCFG_FILES'] = ','.join(loaded_files)

    return merged


def load_to_env(env: Optional[str] = None,
                path: str = '.',
                *,
                override: bool = False) -> Dict[str, str]:
    """Load multi-env config and inject into os.environ.

    Args:
        env: Environment name.
        path: Base directory.
        override: If True, override existing env vars.

    Returns:
        The loaded config dict.
    """
    config = load(env=env, path=path, override_system=override)
    for k, v in config.items():
        if k.startswith('_DOTCFG_'):
            continue
        if override or k not in os.environ:
            os.environ[k] = v
    return config


def available_environments(path: str = '.') -> List[str]:
    """List all environments with config files in the directory.

    Scans for .env.{name} files and returns the environment names.
    """
    base_dir = os.path.abspath(path)
    envs = set()
    try:
        for name in os.listdir(base_dir):
            if name.startswith('.env.') and not name.endswith('.local'):
                env_name = name[5:]  # strip '.env.'
                if env_name and not env_name.startswith('.'):
                    envs.add(env_name)
    except OSError:
        pass
    return sorted(envs)


def diff(env_a: str, env_b: str,
         path: str = '.') -> Dict[str, Dict[str, Optional[str]]]:
    """Compare config between two environments.

    Returns dict of keys that differ:
        {key: {'a': value_in_a, 'b': value_in_b}}
    Keys present in only one env have None for the other.
    """
    config_a = load(env=env_a, path=path)
    config_b = load(env=env_b, path=path)

    all_keys = set(config_a.keys()) | set(config_b.keys())
    differences = {}

    for key in sorted(all_keys):
        if key.startswith('_DOTCFG_'):
            continue
        val_a = config_a.get(key)
        val_b = config_b.get(key)
        if val_a != val_b:
            differences[key] = {'a': val_a, 'b': val_b}

    return differences


def generate_example(path: str = '.',
                     output: Optional[str] = None,
                     *,
                     env: Optional[str] = None) -> str:
    """Generate .env.example from current config.

    Creates a template with all keys but masked values.

    Args:
        path: Base directory.
        output: Output file path (default: {path}/.env.example).
        env: Environment to use as source.

    Returns:
        Path to generated file.
    """
    config = load(env=env, path=path)
    out_path = output or os.path.join(os.path.abspath(path), '.env.example')

    lines = ['# Auto-generated environment template\n',
             f'# Environment: {config.get("_DOTCFG_ENV", "unknown")}\n\n']

    sensitive_patterns = ['KEY', 'SECRET', 'PASSWORD', 'TOKEN', 'PRIVATE']

    for key in sorted(config.keys()):
        if key.startswith('_DOTCFG_'):
            continue
        value = config[key]
        is_sensitive = any(p in key.upper() for p in sensitive_patterns)
        if is_sensitive:
            lines.append(f'{key}=\n')
        else:
            lines.append(f'{key}={value}\n')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return out_path
