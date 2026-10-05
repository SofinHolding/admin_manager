"""Rate limit theo (IP, hành động) trong bộ nhớ — cùng hành vi legacy.

Không persist: restart là reset. Chặn brute-force tự động; chống DDoS là việc của gateway/firewall.
Nhiều instance thì mỗi instance đếm riêng (ghi nhận là giới hạn đã biết; chuyển sang Redis khi scale).
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable

from identity_service.errors import DomainError


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str, action: str, max_hits: int, window_s: int) -> None:
        key = f"{ip}:{action}"
        now = self._clock()
        recent = [t for t in self._hits[key] if t > now - window_s]
        if len(recent) >= max_hits:
            self._hits[key] = recent
            retry = int(window_s - (now - recent[0]))
            raise DomainError(429, f"Quá nhiều lần thử. Đợi {retry} giây.",
                              headers={"Retry-After": str(retry)})
        recent.append(now)
        self._hits[key] = recent
        if len(self._hits) > 10_000:  # dọn khóa đã hết cửa sổ để không phình bộ nhớ
            self._gc(now, window_s)

    def _gc(self, now: float, window_s: int) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - window_s]:
            del self._hits[key]
