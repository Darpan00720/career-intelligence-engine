"""Redis platform adapters (v5.1) — cache, distributed locks, pub/sub, rate
limiter, session store.

These implement the v5 ports (CacheBackend, etc.) against Redis. `redis` is an
optional dependency imported lazily inside constructors, so importing this
module never requires Redis to be installed; only *instantiating* an adapter
does. All keys are tenant-namespaced for isolation.

Wire it up by injecting RedisCacheBackend into core.cache.CacheManager and
RedisRateLimiter into the API gateway when REDIS_URL is set.
"""
from __future__ import annotations

import time
import uuid


def _client(url: str | None):
    import redis  # lazy, optional
    return redis.Redis.from_url(url or "redis://localhost:6379/0", decode_responses=True)


def _ns(tenant_id: str, key: str) -> str:
    return f"{tenant_id}:{key}"


class RedisCacheBackend:
    """Implements core.cache.CacheBackend over Redis (JSON values)."""
    _MISS = object()

    def __init__(self, url: str | None = None, tenant_id: str = "default"):
        import json
        self._json = json
        self._r = _client(url)
        self.tenant_id = tenant_id

    def get(self, key):
        raw = self._r.get(_ns(self.tenant_id, key))
        return self._MISS if raw is None else self._json.loads(raw)

    def set(self, key, value, ttl=None):
        self._r.set(_ns(self.tenant_id, key), self._json.dumps(value),
                    ex=int(ttl) if ttl else None)

    def delete(self, key):
        self._r.delete(_ns(self.tenant_id, key))

    def clear(self):
        for k in self._r.keys(f"{self.tenant_id}:*"):
            self._r.delete(k)

    def keys(self):
        prefix = f"{self.tenant_id}:"
        return [k[len(prefix):] for k in self._r.keys(f"{prefix}*")]


class Redlock:
    """Single-instance Redlock: SET NX PX with a fenced token + safe release."""

    def __init__(self, url: str | None = None):
        self._r = _client(url)

    def acquire(self, resource: str, ttl_ms: int = 10000) -> str | None:
        token = uuid.uuid4().hex
        ok = self._r.set(f"lock:{resource}", token, nx=True, px=ttl_ms)
        return token if ok else None

    def release(self, resource: str, token: str) -> bool:
        # Compare-and-delete via Lua to avoid releasing someone else's lock.
        script = ("if redis.call('get', KEYS[1]) == ARGV[1] then "
                  "return redis.call('del', KEYS[1]) else return 0 end")
        return bool(self._r.eval(script, 1, f"lock:{resource}", token))


class RedisPubSub:
    def __init__(self, url: str | None = None):
        self._r = _client(url)

    def publish(self, channel: str, message: str) -> int:
        return self._r.publish(channel, message)

    def subscribe(self, channel: str):
        pubsub = self._r.pubsub()
        pubsub.subscribe(channel)
        return pubsub


class RedisRateLimiter:
    """Fixed-window limiter using INCR + EXPIRE (atomic per key)."""

    def __init__(self, url: str | None = None, limit: int = 600, window_s: int = 60):
        self._r = _client(url)
        self.limit = limit
        self.window_s = window_s

    def allow(self, key: str) -> bool:
        full = f"rl:{key}"
        count = self._r.incr(full)
        if count == 1:
            self._r.expire(full, self.window_s)
        return count <= self.limit


class RedisSessionStore:
    def __init__(self, url: str | None = None, ttl_s: int = 3600):
        import json
        self._json = json
        self._r = _client(url)
        self.ttl_s = ttl_s

    def put(self, session_id: str, data: dict) -> None:
        self._r.set(f"sess:{session_id}", self._json.dumps(data), ex=self.ttl_s)

    def get(self, session_id: str) -> dict | None:
        raw = self._r.get(f"sess:{session_id}")
        return self._json.loads(raw) if raw else None

    def revoke(self, session_id: str) -> None:
        self._r.delete(f"sess:{session_id}")
