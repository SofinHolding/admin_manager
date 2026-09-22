"""Khoá chống trùng + nonce duy nhất — dịch 1-1 từ `src/reward/domain/idempotency.js`.

Công thức khoá GIỮ NGUYÊN: `sha256(f"{job_id}|{row_index}|{normalized_username}|{point}")`.
`next_nonce` là snowflake giả duy nhất tuyệt đối trong tiến trình (thay vì mốc thời gian đơn thuần
vốn có thể trùng khi gọi liên tiếp trong cùng millisecond).
"""

from __future__ import annotations

import hashlib
import time

_DISCORD_EPOCH_MS = 1420070400000
_seq = 0


def item_key(*, job_id: int, row_index: int, normalized_username: str, point: int) -> str:
    """Khoá idempotency cho một item — cưỡng chế UNIQUE ở DB (`ux_reward_items_idem`)."""
    raw = f"{job_id}|{row_index}|{normalized_username}|{point}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def next_nonce() -> str:
    """Snowflake giả: `(ms_since_discord_epoch << 22) | (seq & 0x3FFFFF)`, `seq` tăng dần trong tiến trình."""
    global _seq
    ms = int(time.time() * 1000) - _DISCORD_EPOCH_MS
    value = (ms << 22) | (_seq & 0x3FFFFF)
    _seq = (_seq + 1) & 0x3FFFFF
    return str(value)
