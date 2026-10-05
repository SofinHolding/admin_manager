"""Rate limit nền theo (IP, nhóm route): token bucket trong bộ nhớ.

KHÔNG thay thế rate limit của service (theo user/key/Redis). Chỉ là lớp chặn lạm dụng thô ở biên:
login/register/reset chặt hơn, phần còn lại rộng. Bộ nhớ bị chặn trên (tự dọn khi quá nhiều khoá).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

STRICT_PATHS = frozenset({
    "/v1/auth/login", "/v1/auth/register", "/v1/auth/forgot-password", "/v1/auth/reset-password",
    "/v1/auth/resend-verify", "/v1/auth/validate-key", "/v1/auth/check-username", "/v1/auth/check-email",
    "/v1/auth/change-password", "/v1/auth/change-password/send-code", "/v1/auth/verify-email",
    "/v1/reward/auth/login", "/oauth/token",
})
_AUTH_PREFIXES = ("/v1/auth", "/v1/reward/auth", "/oauth")
_MAX_KEYS = 50_000


def classify(path: str) -> str:
    p = path.lower().rstrip("/") or "/"
    if p in STRICT_PATHS:
        return "strict"
    if any(p == pre or p.startswith(pre + "/") for pre in _AUTH_PREFIXES):
        return "auth"
    return "default"


class RateLimiter:
    def __init__(self, *, strict_per_min: int, auth_per_min: int, default_per_min: int,
                 enabled: bool = True, clock: Callable[[], float] = time.monotonic) -> None:
        self._limits = {"strict": strict_per_min, "auth": auth_per_min, "default": default_per_min}
        self._enabled = enabled
        self._clock = clock
        self._buckets: dict[tuple[str, str], list[float]] = {}  # key -> [tokens, last_ts]

    def check(self, ip: str, path: str) -> int | None:
        """None nếu được phép; ngược lại số giây `Retry-After`."""
        if not self._enabled:
            return None
        group = classify(path)
        limit = self._limits[group]
        rate = limit / 60.0
        now = self._clock()
        key = (group, ip)
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= _MAX_KEYS:
                self._purge(now)
            bucket = self._buckets[key] = [float(limit), now]
        tokens = min(float(limit), bucket[0] + (now - bucket[1]) * rate)
        bucket[1] = now
        if tokens >= 1.0:
            bucket[0] = tokens - 1.0
            return None
        bucket[0] = tokens
        return max(1, math.ceil((1.0 - tokens) / rate))

    def _purge(self, now: float) -> None:
        # Bucket đã đầy lại = không còn thông tin hữu ích; nếu vẫn quá nhiều thì bỏ nửa cũ nhất.
        full = [k for k, (t, ts) in self._buckets.items()
                if t + (now - ts) * (self._limits[k[0]] / 60.0) >= self._limits[k[0]]]
        for k in full:
            del self._buckets[k]
        if len(self._buckets) >= _MAX_KEYS:
            for k in sorted(self._buckets, key=lambda x: self._buckets[x][1])[: _MAX_KEYS // 2]:
                del self._buckets[k]
