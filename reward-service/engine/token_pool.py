"""Bể token Discord cho một job — xoay vòng NGẪU NHIÊN giữa các tài khoản khi trả điểm.

Mỗi token có `GatewaySession` riêng (session_id gắn với token gọi `POST /interactions`). Quy luật:

* **Chọn token ngẫu nhiên** trong số token đang sẵn sàng, KHÔNG dùng 1 token 2 lần liên tiếp khi còn token
  khác sẵn sàng ⇒ các tài khoản xen kẽ nhau, không theo thứ tự cố định như hàng đợi.
* **Khoảng cách giữa 2 lệnh liên tiếp (bất kể token) ngẫu nhiên** trong [0.5, 1.5] × `spread`, với
  `spread = (delay_ms + jitter_ms/2) / số token dùng được`. N token ⇒ nhịp chung nhanh gấp ~N lần so với
  1 token, nhưng mỗi lệnh cách nhau không đều.
* **Nhịp RIÊNG từng token** (bảo vệ tài khoản): sau khi 1 token xong 1 item, nó chỉ được chọn lại sau
  `delay_ms + random(0, jitter_ms)` ⇒ mỗi tài khoản không bao giờ gửi nhanh hơn nhịp cấu hình. 1 token ⇒
  hành vi y hệt trước đây.
* Token bị Discord từ chối (401/403/404) hoặc gây chuỗi `unknown` liên tiếp ⇒ `disable` cho phần còn lại
  của lần chạy; token bị 429 ⇒ `cool` tạm thời. Hết token dùng được ⇒ `acquire` trả `None` và runner mới
  tạm dừng job.

Job vẫn xử lý TUẦN TỰ từng item (một lệnh + một lần xác nhận tại một thời điểm) — xác nhận reply liên kết
theo bot_id/anchor nên chạy song song sẽ làm nhầm reply giữa các item. Tốc độ tối đa = thời gian bot trả lời.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from discord.gateway_session import GatewaySession

_POLL_STEP_S = 0.25
_GAP_MIN_FACTOR = 0.5
_GAP_MAX_FACTOR = 1.5


@dataclass(eq=False)
class TokenSlot:
    token_id: int
    label: str
    token: str = field(repr=False)  # token KHÔNG BAO GIỜ vào repr/log
    session: GatewaySession
    ready_at: float = 0.0       # monotonic: sớm nhất được dùng lại (nhịp delay của riêng token)
    cooldown_until: float = 0.0  # monotonic: bị 429 → nghỉ tới thời điểm này
    last_used_seq: int = 0
    unknown_ids: list[int] = field(default_factory=list)  # item `unknown` liên tiếp gần nhất của token này
    session_failures: int = 0   # số lần liên tiếp Gateway của token không kết nối được
    disabled: bool = False
    disabled_reason: str | None = None


class TokenPool:
    def __init__(
        self, slots: list[TokenSlot], *, delay_ms: int, jitter_ms: int,
        clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None,
    ) -> None:
        self._slots = slots
        self._delay_ms = delay_ms
        self._jitter_ms = jitter_ms
        self._clock = clock
        self._rng = rng or random.Random()
        self._seq = itertools.count(1)
        self._last: TokenSlot | None = None
        self._next_send_at = 0.0  # monotonic: sớm nhất được gửi lệnh kế tiếp (nhịp chung của cả bể)

    @property
    def slots(self) -> list[TokenSlot]:
        return list(self._slots)

    def usable(self) -> list[TokenSlot]:
        return [s for s in self._slots if not s.disabled]

    def has_usable(self) -> bool:
        return any(not s.disabled for s in self._slots)

    def _available_at(self, slot: TokenSlot) -> float:
        return max(slot.ready_at, slot.cooldown_until)

    def _pick(self, ready: list[TokenSlot]) -> TokenSlot:
        """Ngẫu nhiên đều trong số token sẵn sàng, tránh lặp lại token vừa dùng nếu còn lựa chọn khác."""
        candidates = [s for s in ready if s is not self._last] or ready
        return self._rng.choice(candidates)

    def _gap_s(self, usable_count: int) -> float:
        spread_s = (self._delay_ms + self._jitter_ms / 2) / 1000 / max(1, usable_count)
        return self._rng.uniform(_GAP_MIN_FACTOR * spread_s, _GAP_MAX_FACTOR * spread_s)

    async def acquire(self, should_stop: Callable[[], bool]) -> TokenSlot | None:
        """Trả token được chọn ngẫu nhiên khi tới nhịp chung VÀ có token sẵn sàng. Chờ nếu chưa tới nhịp.
        `None` ⇒ hết token dùng được hoặc `should_stop()` (stop/pause) bật trong lúc chờ."""
        while True:
            if should_stop():
                return None
            usable = self.usable()
            if not usable:
                return None
            now = self._clock()
            ready = [s for s in usable if self._available_at(s) <= now]
            if ready and now >= self._next_send_at:
                slot = self._pick(ready)
                slot.last_used_seq = next(self._seq)
                self._last = slot
                self._next_send_at = now + self._gap_s(len(usable))
                return slot
            wake_at = self._next_send_at if ready else min(
                max(self._next_send_at, self._available_at(s)) for s in usable)
            await asyncio.sleep(max(0.0, min(wake_at - now, _POLL_STEP_S)))

    def release(self, slot: TokenSlot) -> None:
        """Đánh dấu token vừa dùng xong: chỉ được chọn lại sau `delay + jitter` (nhịp riêng của token)."""
        jitter = self._rng.uniform(0, self._jitter_ms) if self._jitter_ms > 0 else 0
        slot.ready_at = self._clock() + (self._delay_ms + jitter) / 1000

    def cool(self, slot: TokenSlot, seconds: float) -> None:
        slot.cooldown_until = max(slot.cooldown_until, self._clock() + seconds)

    def disable(self, slot: TokenSlot, reason: str) -> None:
        slot.disabled = True
        slot.disabled_reason = reason

    async def close(self) -> None:
        for slot in self._slots:
            try:
                await slot.session.close()
            except Exception:  # noqa: BLE001 — đóng bể không được làm hỏng việc dọn dẹp còn lại
                pass
