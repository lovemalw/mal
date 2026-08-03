"""File watcher with hot-reload support.

Monitors .env files for changes and triggers callbacks or
auto-reloads the config. Uses polling (cross-platform) with
optional native OS notifications where available.

Usage:
    from dotcfg import watcher

    # Watch a file and auto-reload on change
    w = watcher.FileWatcher('.env', on_change=lambda cfg: print("Reloaded!"))
    w.start()

    # Or use the context manager
    with watcher.auto_reload('.env') as cfg:
        print(cfg['DATABASE_URL'])
"""

from __future__ import annotations

import os
import time
import threading
import hashlib
from typing import Any, Callable, Dict, List, Optional, Set

from .core import EnvCore


class FileWatcher:
    """Watch one or more files for modifications.

    Uses stat-based polling for maximum cross-platform compatibility.
    Detects changes via mtime + content hash (handles clock skew).
    """

    def __init__(self,
                 paths: str | List[str],
                 *,
                 interval: float = 1.0,
                 on_change: Optional[Callable[[Dict[str, str]], Any]] = None,
                 on_error: Optional[Callable[[Exception], Any]] = None,
                 auto_reload: bool = True):
        """
        Args:
            paths: File path(s) to watch.
            interval: Polling interval in seconds.
            on_change: Callback receiving the new parsed config.
            on_error: Callback for file read errors.
            auto_reload: If True, automatically re-parse on change.
        """
        if isinstance(paths, str):
            paths = [paths]
        self._paths = [os.path.abspath(p) for p in paths]
        self._interval = max(0.1, interval)
        self._on_change = on_change
        self._on_error = on_error
        self._auto_reload = auto_reload
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._state: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._config: Dict[str, str] = {}

    def _file_hash(self, path: str) -> str:
        """Compute content hash for change detection."""
        try:
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ''

    def _snapshot(self) -> Dict[str, str]:
        """Get current file states."""
        snap = {}
        for p in self._paths:
            try:
                mt = str(os.path.getmtime(p))
                snap[p] = mt + ':' + self._file_hash(p)
            except OSError:
                snap[p] = 'missing'
        return snap

    def _load_config(self) -> Dict[str, str]:
        """Load and merge all watched env files."""
        merged = {}
        for p in self._paths:
            try:
                core = EnvCore()
                core.load(p)
                merged.update(dict(core.items()))
            except Exception as e:
                if self._on_error:
                    self._on_error(e)
        return merged

    def _poll_loop(self) -> None:
        """Main polling loop."""
        self._state = self._snapshot()
        if self._auto_reload:
            with self._lock:
                self._config = self._load_config()

        while self._running:
            time.sleep(self._interval)
            new_state = self._snapshot()
            if new_state != self._state:
                self._state = new_state
                if self._auto_reload:
                    cfg = self._load_config()
                    with self._lock:
                        self._config = cfg
                    if self._on_change:
                        try:
                            self._on_change(cfg)
                        except Exception:
                            pass
                elif self._on_change:
                    try:
                        self._on_change({})
                    except Exception:
                        pass

    def start(self) -> 'FileWatcher':
        """Start watching in a background thread."""
        if self._running:
            return self
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name=f'dotcfg-watcher-{id(self):x}'
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the watcher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None

    @property
    def config(self) -> Dict[str, str]:
        """Get the current config (thread-safe)."""
        with self._lock:
            return self._config.copy()

    @property
    def is_running(self) -> bool:
        return self._running

    def __enter__(self) -> 'FileWatcher':
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()


class _AutoReloadProxy:
    """Proxy object that always returns the latest config value."""

    def __init__(self, fw: FileWatcher):
        self._fw = fw

    def __getitem__(self, key: str) -> str:
        return self._fw.config.get(key, '')

    def __contains__(self, key: str) -> bool:
        return key in self._fw.config

    def get(self, key: str, default: str = '') -> str:
        return self._fw.config.get(key, default)

    def items(self):
        return self._fw.config.items()

    def keys(self):
        return self._fw.config.keys()

    def values(self):
        return self._fw.config.values()

    def __repr__(self) -> str:
        return f'AutoReloadConfig({len(self._fw.config)} vars)'


def auto_reload(path: str | List[str],
                interval: float = 1.0) -> FileWatcher:
    """Create and start a file watcher with auto-reload.

    Returns a FileWatcher whose .config property always
    reflects the latest file contents.

    Usage:
        with auto_reload('.env') as w:
            print(w.config['DB_URL'])
    """
    return FileWatcher(path, interval=interval, auto_reload=True).start()


def on_change(path: str | List[str],
              callback: Callable[[Dict[str, str]], Any],
              interval: float = 1.0) -> FileWatcher:
    """Watch files and call callback on any change."""
    return FileWatcher(path, on_change=callback, interval=interval).start()
