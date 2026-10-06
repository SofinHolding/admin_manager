"""Xử lý event Identity -> `account_projection` (bản sao cục bộ username/role/status)."""

from __future__ import annotations

import asyncpg

IDENTITY_TYPES = ("account.registered", "account.status_changed", "account.role_changed", "account.profile_updated",
                  "account.deleted")
INTEGRATION_TYPES = ("snapshot.committed", "resource.changed")


class EventInvalid(ValueError):
    """Event sai hợp đồng (thiếu trường bắt buộc)."""


def _data(event: dict) -> dict:
    data = event.get("data")
    if not isinstance(data, dict):
        raise EventInvalid("event thiếu `data`")
    return data


def _account_id(data: dict) -> str:
    value = data.get("account_id")
    if not isinstance(value, str) or not value.strip():
        raise EventInvalid("event thiếu `account_id`")
    return value.strip()


# Cột của `account_projection` mà từng loại event GHI. `registered` ghi cả ba; các loại còn lại mỗi loại một cột.
_COLUMNS = {
    "account.registered": ("username", "role", "status"),
    "account.profile_updated": ("username",),
    "account.role_changed": ("role",),
    "account.status_changed": ("status",),
}
# Loại event có thể ghi từng cột: replay event CŨ chỉ được ghi cột chưa bị event MỚI hơn (cùng account) ghi.
_WRITERS = {"username": {"account.registered", "account.profile_updated"},
            "role": {"account.registered", "account.role_changed"},
            "status": {"account.registered", "account.status_changed"}}


def _value(etype: str, column: str, data: dict) -> str:
    return str(data.get(column) or "")


async def apply_identity_event(conn: asyncpg.Connection, event: dict, *,
                               newer_types: frozenset[str] = frozenset()) -> bool:
    """Trả True nếu event làm đổi projection (để tăng seal).

    `newer_types`: các loại event cùng account, seq LỚN HƠN, ĐÃ được áp (chỉ truyền khi replay dead-letter). Cột nào đã bị
    một loại như vậy ghi thì event cũ KHÔNG ghi đè nữa; cột khác của cùng event vẫn áp (không bỏ event theo cả khối)."""
    etype = event.get("type")
    data = _data(event)
    if etype in _COLUMNS:
        aid = _account_id(data)
        cols = [c for c in _COLUMNS[etype] if not (_WRITERS[c] & newer_types)]
        if not cols:
            return False
        names = ", ".join(cols)
        marks = ", ".join(f"${i + 2}" for i in range(len(cols)))
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        await conn.execute(
            f"INSERT INTO account_projection (account_id, {names}, updated_at) VALUES ($1, {marks}, now())"  # noqa: S608
            f" ON CONFLICT (account_id) DO UPDATE SET {sets}, updated_at = now()",
            aid, *[_value(etype, c, data) for c in cols])
        return True       # suspended vẫn GIỮ dữ liệu; chỉ cập nhật trạng thái
    if etype == "account.deleted":
        aid = _account_id(data)
        await conn.execute(
            "INSERT INTO account_projection (account_id, deleted) VALUES ($1, true)"
            " ON CONFLICT (account_id) DO UPDATE SET deleted = true, updated_at = now()", aid)
        return True       # ẩn dữ liệu owner khỏi so sánh người; trạng thái cuối nên luôn áp
    return False          # loại event khác: không việc gì phải làm


def validate_integration_event(event: dict) -> None:
    """Kiểm trường bắt buộc TRƯỚC khi áp dụng nguyên tử: `type` (chuỗi không rỗng), `data` (object) và, với
    `snapshot.committed`, `snapshot_id`. Event hỏng bị dead-letter sau số lần thử; nếu lọt vào bước áp dụng, `mark_processed`
    ném lỗi và rollback cả vòng — consumer kẹt vĩnh viễn ở event đó. Type lạ (nguồn mới hơn) là no-op hợp lệ."""
    etype = event.get("type")
    if not isinstance(etype, str) or not etype.strip():
        raise EventInvalid("event thiếu `type` hợp lệ")
    data = _data(event)
    if etype == "snapshot.committed":
        sid = data.get("snapshot_id")
        if not isinstance(sid, str) or not sid.strip():
            raise EventInvalid("snapshot.committed thiếu `snapshot_id`")
