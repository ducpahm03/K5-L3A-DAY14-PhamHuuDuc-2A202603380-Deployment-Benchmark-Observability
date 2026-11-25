"""
Rate limiting (tiêu chí 7).

Sliding window 60 giây lưu trong Redis sorted set (atomic qua Lua script),
đảm bảo stateless giữa nhiều worker / instance và giữ nguyên state sau restart.
"""
import hashlib
import logging
import math
import threading
import time
import uuid
from collections import deque

from fastapi import HTTPException, status

from app.config import settings
from app.redis_client import r

logger = logging.getLogger("agent.rate_limiter")

WINDOW_SECONDS = 60.0

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = window
    if oldest and #oldest >= 2 then
        retry_after = math.max(1, math.ceil(window - (now - tonumber(oldest[2]))))
    end
    return {0, count, retry_after}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window) + 5)
return {1, count + 1, 0}
"""

_local_lock = threading.Lock()
_local_buckets: dict[str, deque[float]] = {}


def _key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


def _check_memory_fallback(bucket_id: str, limit: int, now: float) -> None:
    with _local_lock:
        q = _local_buckets.setdefault(bucket_id, deque())
        cutoff = now - WINDOW_SECONDS
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            retry_after = max(1, math.ceil(WINDOW_SECONDS - (now - q[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)


def check_rate_limit(key: str) -> None:
    """Kiểm tra giới hạn RATE_LIMIT_PER_MINUTE req / 60s cho mỗi API key."""
    limit = settings.rate_limit_per_minute
    now = time.time()
    bucket_id = _key_hash(key)
    redis_key = f"ratelimit:{bucket_id}"

    if r is not None:
        try:
            member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
            allowed, _, retry_after = r.eval(
                _SLIDING_WINDOW_LUA,
                1,
                redis_key,
                str(now),
                str(WINDOW_SECONDS),
                str(limit),
                member,
            )
            if int(allowed) == 0:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(max(1, int(retry_after)))},
                )
            return
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("rate_limit_redis_fallback", extra={"error": str(exc)})

    _check_memory_fallback(bucket_id, limit, now)
