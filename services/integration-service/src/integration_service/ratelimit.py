"""Rate limit trong bộ nhớ (cửa sổ cố định theo phút) cho mặt phẳng máy.

Giới hạn theo `client_id` (mặc định 600 request/phút) và giới hạn lần xác thực SAI theo IP (chặn dò khoá).
Mỗi tiến trình có bộ đếm riêng; chạy nhiều worker thì trần thực tế = trần × số worker (ghi nhận ở README).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from manager_contracts import ErrorCode

from integration_service.errors import ApiProblem

WINDOW_S = 60.0


class _Window:
    __slots__ = ("start", "count")

    def __init__(self, start: float) -> None:
        self.start = start
        self.count = 0


class FixedWindowLimiter:
    def __init__(self, limit: int, *, clock: Callable[[], float] = time.monotonic, window: float = WINDOW_S) -> None:
        self.limit = limit
        self._clock = clock
        self._window = window
        self._buckets: dict[str, _Window] = {}

    def _bucket(self, key: str) -> _Window:
        now = self._clock()
        b = self._buckets.get(key)
        if b is None or now - b.start >= self._window:
            if len(self._buckets) > 50_000:  # dọn khi quá lớn để không phình bộ nhớ
                self._buckets = {k: v for k, v in self._buckets.items() if now - v.start < self._window}
            b = self._buckets[key] = _Window(now)
        return b

    def retry_after(self, key: str) -> int:
        return max(1, math.ceil(self._window - (self._clock() - self._bucket(key).start)))

    def hit(self, key: str) -> bool:
        """Ghi một request; False nếu đã vượt trần."""
        b = self._bucket(key)
        b.count += 1
        return b.count <= self.limit

    def exceeded(self, key: str) -> bool:
        return self._bucket(key).count >= self.limit

    def check(self, key: str, message: str = "Vượt giới hạn request") -> None:
        if not self.hit(key):
            raise ApiProblem(ErrorCode.RATE_LIMITED, message, status_code=429,
                             headers={"Retry-After": str(self.retry_after(key))})
