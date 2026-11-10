import time
from collections import defaultdict, deque

from fastapi import Request

from app.config import get_settings
from app.errors import ApiError

settings = get_settings()
_hits: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limits() -> None:
    _hits.clear()


async def auth_rate_limit(request: Request) -> None:
    """Sliding-window per-IP limiter for unauthenticated auth endpoints (per process)."""
    if not settings.rate_limit_enabled:
        return
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{request.url.path}"
    now = time.monotonic()
    bucket = _hits[key]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_minute:
        retry = max(1, int(60 - (now - bucket[0])))
        raise ApiError(
            429, "rate_limited", "Too many requests. Try again shortly.", {"Retry-After": str(retry)}
        )
    bucket.append(now)
    if len(_hits) > 10_000:
        for k in [k for k, v in _hits.items() if not v or now - v[-1] > 60]:
            _hits.pop(k, None)
