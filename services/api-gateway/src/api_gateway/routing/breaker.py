"""Circuit breaker đơn giản cho từng upstream: closed → open (sau N lỗi liên tiếp) → half-open (thử 1 request)."""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown_s: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._threshold = threshold
        self._cooldown = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_started: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._probe_started is not None and self._clock() - self._probe_started < self._cooldown:
            return "half_open"
        return "open" if self._clock() - self._opened_at < self._cooldown else "half_open"

    def retry_after(self) -> int:
        if self._opened_at is None:
            return 1
        return max(1, int(self._cooldown - (self._clock() - self._opened_at)) + 1)

    def allow(self) -> bool:
        """True nếu được gọi upstream. Khi half-open chỉ cho đúng MỘT request thăm dò tại một thời điểm."""
        if self._opened_at is None:
            return True
        now = self._clock()
        if now - self._opened_at < self._cooldown:
            return False
        if self._probe_started is not None and now - self._probe_started < self._cooldown:
            return False  # đã có request thăm dò đang chạy
        self._probe_started = now
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_started = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._opened_at is not None or self._failures >= self._threshold:
            self._opened_at = self._clock()  # mở lại (kể cả khi thăm dò half-open thất bại)
            self._probe_started = None
