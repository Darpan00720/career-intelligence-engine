"""CacheManager (v5) — TTL cache with invalidation, dedup, and locks.

Backends sit behind a small port so memory (default) and Redis are
interchangeable. Memory backend is thread-safe and fully featured for single
node; Redis backend is provided for horizontal scaling (lazy-imported, optional).

Features: TTL, key/prefix invalidation, cache warming, distributed locks, and
single-flight request deduplication (get_or_set computes once under contention).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class CacheBackend:
    def get(self, key: str) -> Any: raise NotImplementedError
    def set(self, key: str, value: Any, ttl: float | None = None) -> None: raise NotImplementedError
    def delete(self, key: str) -> None: raise NotImplementedError
    def clear(self) -> None: raise NotImplementedError
    def keys(self) -> list[str]: raise NotImplementedError


class MemoryBackend(CacheBackend):
    _MISS = object()

    def __init__(self):
        self._store: dict[str, tuple[Any, float | None]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return self._MISS
            value, expires = item
            if expires is not None and time.time() > expires:
                self._store.pop(key, None)
                return self._MISS
            return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            self._store[key] = (value, time.time() + ttl if ttl else None)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())


class RedisBackend(CacheBackend):  # pragma: no cover - requires a live Redis
    """Optional Redis backend. Values are JSON-encoded. Lazy import keeps redis optional."""
    _MISS = MemoryBackend._MISS

    def __init__(self, url: str | None = None):
        import json
        import redis  # type: ignore
        self._json = json
        self._client = redis.Redis.from_url(url or "redis://localhost:6379/0")

    def get(self, key: str) -> Any:
        raw = self._client.get(key)
        return self._MISS if raw is None else self._json.loads(raw)

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self._client.set(key, self._json.dumps(value), ex=int(ttl) if ttl else None)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def clear(self) -> None:
        self._client.flushdb()

    def keys(self) -> list[str]:
        return [k.decode() for k in self._client.keys("*")]


class CacheManager:
    MISS = MemoryBackend._MISS

    def __init__(self, backend: CacheBackend | None = None):
        self.backend = backend or MemoryBackend()
        self.hits = 0
        self.misses = 0
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── Basic ops ───────────────────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        value = self.backend.get(key)
        if value is MemoryBackend._MISS:
            self.misses += 1
            return default
        self.hits += 1
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self.backend.set(key, value, ttl)

    def delete(self, key: str) -> None:
        self.backend.delete(key)

    def invalidate(self, prefix: str = "") -> int:
        """Delete all keys (optionally those starting with prefix). Returns count."""
        keys = [k for k in self.backend.keys() if not prefix or k.startswith(prefix)]
        for k in keys:
            self.backend.delete(k)
        return len(keys)

    # ── Distributed lock (single-node impl; Redis SETNX for multi-node) ─────────
    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    # ── Single-flight dedup ─────────────────────────────────────────────────────
    def get_or_set(self, key: str, producer: Callable[[], Any],
                   ttl: float | None = None) -> Any:
        """Return cached value or compute it exactly once under contention."""
        value = self.backend.get(key)
        if value is not MemoryBackend._MISS:
            self.hits += 1
            return value
        lock = self._lock_for(key)
        with lock:
            # Re-check after acquiring the lock (another thread may have filled it).
            value = self.backend.get(key)
            if value is not MemoryBackend._MISS:
                self.hits += 1
                return value
            self.misses += 1
            produced = producer()
            self.backend.set(key, produced, ttl)
            return produced

    # ── Warming + stats ─────────────────────────────────────────────────────────
    def warm(self, mapping: dict[str, Any], ttl: float | None = None) -> int:
        for k, v in mapping.items():
            self.set(k, v, ttl)
        return len(mapping)

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses, "hit_ratio": self.hit_ratio,
                "size": len(self.backend.keys())}


_cache: CacheManager | None = None


def get_cache() -> CacheManager:
    global _cache
    if _cache is None:
        _cache = CacheManager()
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None
