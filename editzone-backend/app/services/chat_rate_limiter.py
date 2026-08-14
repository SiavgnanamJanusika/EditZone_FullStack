"""Distributed Socket.IO rate limits with a development-only local fallback."""
import asyncio
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings

_local_counts: dict[str, tuple[int, float]] = {}
_local_lock = asyncio.Lock()
_redis_unavailable_until = 0.0


async def _local_allow(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    async with _local_lock:
        count, expires = _local_counts.get(key, (0, now + window_seconds))
        if expires <= now:
            count, expires = 0, now + window_seconds
        count += 1
        _local_counts[key] = (count, expires)
        return count <= limit


async def allow_chat_event(scope: str, identifier: str, limit: int) -> bool:
    """Increment a fixed-window counter. Production fails closed if Redis fails."""
    window = max(1, settings.CHAT_RATE_LIMIT_WINDOW_SECONDS)
    bucket = int(time.time()) // window
    key = f"editzone:chat-rate:{scope}:{identifier}:{bucket}"
    global _redis_unavailable_until
    # Development uses the same fixed-window semantics without requiring a
    # local Redis daemon. Staging/production always use distributed counters.
    if settings.ENV.lower() not in {"production", "staging"}:
        return await _local_allow(key, limit, window)
    redis = Redis.from_url(
        settings.REDIS_URL, decode_responses=True,
        socket_connect_timeout=0.2, socket_timeout=0.2,
    )
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window + 1)
            count, _ = await pipe.execute()
        return int(count) <= limit
    except RedisError:
        if settings.ENV.lower() in {"production", "staging"}:
            return False
        _redis_unavailable_until = time.monotonic() + 5
        return await _local_allow(key, limit, window)
    finally:
        await redis.aclose()
