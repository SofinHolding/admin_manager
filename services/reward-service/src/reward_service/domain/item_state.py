"""State machine cho item reward — dịch 1-1 từ `src/reward/domain/item-state.js`.

`success` là hấp thụ (không có cạnh ra); `processing -> pending` BỊ CẤM (chống mất write-ahead);
`unknown -> success|pending` chỉ được phép khi `actor='operator'` (item unknown không bao giờ tự
động gửi lại — chỉ người vận hành mới được đưa nó trở lại hàng đợi).
"""

from __future__ import annotations

TRANSITIONS: dict[str, list[str]] = {
    "pending": ["processing"],
    "processing": ["success", "failed", "unknown", "retrying"],
    "retrying": ["processing", "failed"],
    "unknown": ["success", "pending"],
    "success": [],
    "failed": [],
    "skipped": [],
}


def can_transition(from_status: str, to_status: str, actor: str = "system") -> bool:
    """Kiểm tra cạnh chuyển trạng thái có hợp lệ không (theo `TRANSITIONS` + ràng buộc `unknown`)."""
    allowed = TRANSITIONS.get(from_status)
    if not allowed or to_status not in allowed:
        return False
    if from_status == "unknown" and actor != "operator":
        return False
    return True


def assert_transition(from_status: str, to_status: str, actor: str = "system") -> None:
    """Ném `ValueError` nếu cạnh chuyển trạng thái không hợp lệ."""
    if not can_transition(from_status, to_status, actor):
        raise ValueError(f"Invalid item transition {from_status} -> {to_status} (actor={actor})")
