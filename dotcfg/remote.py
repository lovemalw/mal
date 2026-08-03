"""Remote configuration fetching and caching.

Fetch .env-formatted config from HTTP(S) endpoints with
automatic caching, TTL expiry, and fallback to local files.

Usage:
    from dotcfg import remote

    # Fetch remote config with 5-minute cache
    config = remote.fetch("https://config.myapp.com/env/production", ttl=300)

    # With auth header
    config = remote.fetch(url, headers={"Authorization": "Bearer token"})

    # Auto-refresh in background
    remote.watch(url, interval=60, callback=on_config_change)
"""

from __future__ import annotations

import os
import time
import json
import hashlib
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from .core import EnvCore


_cache_lock = threading.Lock()
_cache_store: Dict[str, Tuple[float, Dict[str, str]]] = {}
_watchers: Dict[str, threading.Thread] = {}


def _cache_dir() -> str:
    base = os.environ.get('LOCALAPPDATA',
                          os.environ.get('XDG_CACHE_HOME',
                                         os.path.expanduser('~/.cache')))
    d = os.path.join(base, 'dotcfg', 'remote')
    os.makedirs(d, exist_ok=True)
    return d


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _read_disk_cache(url: str, ttl: float) -> Optional[Dict[str, str]]:
    """Read from disk cache if still valid."""
    path = os.path.join(_cache_dir(), _cache_key(url) + '.json')
    try:
        if not os.path.isfile(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if age > ttl:
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_disk_cache(url: str, data: Dict[str, str]) -> None:
    """Write config to disk cache."""
    path = os.path.join(_cache_dir(), _cache_key(url) + '.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 30) -> Optional[str]:
    """Perform HTTP GET request. Returns response body or None."""
    try:
        from urllib.request import Request, urlopen
        import ssl
        ctx = ssl.create_default_context()
        req_headers = {
            'User-Agent': 'dotcfg/1.1 (remote-config)',
            'Accept': 'text/plain, application/json',
        }
        if headers:
            req_headers.update(headers)
        rq = Request(url, headers=req_headers)
        with urlopen(rq, timeout=timeout, context=ctx) as resp:
            if resp.status == 200:
                return resp.read().decode('utf-8', errors='replace')
    except Exception:
        pass
    return None


def fetch(url: str, *,
          ttl: float = 300.0,
          headers: Optional[Dict[str, str]] = None,
          timeout: int = 30,
          fallback: Optional[str] = None) -> Dict[str, str]:
    """Fetch remote .env config with caching.

    Args:
        url: HTTP(S) endpoint serving .env formatted text or JSON.
        ttl: Cache TTL in seconds (default 5 minutes).
        headers: Additional HTTP headers (e.g. auth tokens).
        timeout: HTTP timeout in seconds.
        fallback: Path to local .env file if remote fails.

    Returns:
        Dictionary of configuration key-value pairs.
    """
    with _cache_lock:
        if url in _cache_store:
            cached_time, cached_data = _cache_store[url]
            if (time.time() - cached_time) < ttl:
                return cached_data.copy()

    disk_cache = _read_disk_cache(url, ttl)
    if disk_cache is not None:
        with _cache_lock:
            _cache_store[url] = (time.time(), disk_cache)
        return disk_cache.copy()

    body = _http_get(url, headers=headers, timeout=timeout)
    if body:
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                result = {str(k): str(v) for k, v in data.items()}
            else:
                result = EnvCore.parse(body)
        except (json.JSONDecodeError, ValueError):
            result = EnvCore.parse(body)

        with _cache_lock:
            _cache_store[url] = (time.time(), result)
        _write_disk_cache(url, result)
        return result.copy()

    if fallback and os.path.isfile(fallback):
        core = EnvCore()
        core.load(fallback)
        return dict(core.items())

    disk_expired = _read_disk_cache(url, float('inf'))
    if disk_expired:
        return disk_expired.copy()

    return {}


def invalidate(url: Optional[str] = None) -> None:
    """Invalidate cache for a URL or all URLs."""
    with _cache_lock:
        if url:
            _cache_store.pop(url, None)
        else:
            _cache_store.clear()


def watch(url: str, *,
          interval: float = 60.0,
          headers: Optional[Dict[str, str]] = None,
          callback: Optional[Callable[[Dict[str, str]], Any]] = None) -> None:
    """Watch a remote config URL for changes.

    Polls the URL at the given interval and calls the callback
    when the config changes. Runs in a daemon background thread.

    Args:
        url: Remote config URL to watch.
        interval: Polling interval in seconds.
        headers: HTTP headers for requests.
        callback: Function called with new config dict on change.
    """
    if url in _watchers and _watchers[url].is_alive():
        return

    def _poll():
        last_hash = ''
        while True:
            time.sleep(interval)
            try:
                data = fetch(url, ttl=0, headers=headers)
                h = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()
                if h != last_hash and last_hash != '':
                    if callback:
                        callback(data)
                last_hash = h
            except Exception:
                pass

    t = threading.Thread(target=_poll, daemon=True, name=f'dotcfg-watch-{_cache_key(url)}')
    t.start()
    _watchers[url] = t


def stop_watch(url: Optional[str] = None) -> None:
    """Stop watching a URL (thread will terminate on next cycle)."""
    if url:
        _watchers.pop(url, None)
    else:
        _watchers.clear()
