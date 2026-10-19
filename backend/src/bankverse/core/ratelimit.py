"""Token-bucket rate limiting (in-process by default, Redis when configured for multi-instance)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from bankverse.core.config import get_settings
from bankverse.core.errors import TooManyRequests


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._redis = None
        url = get_settings().redis_url
        if url:
            import redis

            self._redis = redis.Redis.from_url(url)

    def hit(self, key: str, per_minute: int, cost: float = 1.0) -> None:
        if per_minute <= 0:
            return
        if self._redis is not None:
            self._hit_redis(key, per_minute)
            return
        rate = per_minute / 60.0
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(tokens=float(per_minute), updated=now)
                self._buckets[key] = b
            b.tokens = min(float(per_minute), b.tokens + (now - b.updated) * rate)
            b.updated = now
            if b.tokens < cost:
                retry = int((cost - b.tokens) / rate) + 1
                raise TooManyRequests("Too many requests", details={"retry_after": retry})
            b.tokens -= cost
            if len(self._buckets) > 100_000:  # bound memory
                self._buckets.clear()

    def _hit_redis(self, key: str, per_minute: int) -> None:
        window = int(time.time() // 60)
        rkey = f"rl:{key}:{window}"
        assert self._redis is not None
        pipe = self._redis.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, 120)
        count = pipe.execute()[0]
        if count > per_minute:
            raise TooManyRequests("Too many requests", details={"retry_after": 60 - int(time.time() % 60)})

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


limiter = RateLimiter()
