from __future__ import annotations

import functools
import json
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from tothemoon_api.config import get_settings
from tothemoon_api.external.adapters import refresh_freshness_metadata

redis: Any | None = None
try:
    import redis as redis_module
except ImportError:  # pragma: no cover - exercised when dependency is not installed locally
    pass
else:
    redis = redis_module

T = TypeVar("T")
_MEMORY_CACHE: dict[str, tuple[float, str]] = {}
_REDIS_CLIENT: Any | None = None
_REDIS_DISABLED = False


def _redis_client() -> Any | None:
    global _REDIS_CLIENT, _REDIS_DISABLED
    if _REDIS_DISABLED:
        return None
    settings = get_settings()
    if not settings.redis_url or redis is None:
        _REDIS_DISABLED = True
        return None
    if _REDIS_CLIENT is None:
        try:
            _REDIS_CLIENT = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        except Exception:
            _REDIS_DISABLED = True
            return None
    return _REDIS_CLIENT


def _disable_redis_cache() -> None:
    global _REDIS_CLIENT, _REDIS_DISABLED
    _REDIS_CLIENT = None
    _REDIS_DISABLED = True


def _make_key(namespace: str, args: tuple[object, ...], kwargs: dict[str, object]) -> str:
    raw = json.dumps(
        {"args": args, "kwargs": kwargs},
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"cache:{namespace}:{raw}"


def cached(ttl: int, namespace: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> T:
            key = _make_key(namespace, args, kwargs)
            client = _redis_client()
            if client is not None:
                try:
                    hit = client.get(key)
                    if hit:
                        return cast(T, refresh_freshness_metadata(json.loads(hit)))
                except Exception:
                    _disable_redis_cache()
                    client = None

            expires_at, payload = _MEMORY_CACHE.get(key, (0.0, ""))
            if expires_at > time.monotonic():
                return cast(T, refresh_freshness_metadata(json.loads(payload)))

            value = fn(*args, **kwargs)
            encoded = json.dumps(value, default=str, separators=(",", ":"))
            if client is not None:
                try:
                    client.setex(key, ttl, encoded)
                except Exception:
                    _disable_redis_cache()
            _MEMORY_CACHE[key] = (time.monotonic() + ttl, encoded)
            return cast(T, refresh_freshness_metadata(value))

        return wrapper

    return decorator


def clear_external_cache() -> None:
    global _REDIS_CLIENT, _REDIS_DISABLED
    _MEMORY_CACHE.clear()
    _REDIS_CLIENT = None
    _REDIS_DISABLED = False
