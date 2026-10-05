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


async def apply_identity_event(conn: asyncpg.Connection, event: dict) -> bool:
    """Trả True nếu event làm đổi projection (để tăng seal)."""
    etype = event.get("type")
    data = _data(event)
    if etype == "account.registered":
        aid = _account_id(data)
        await conn.execute(
            "INSERT INTO account_projection (account_id, username, role, status, updated_at) VALUES ($1,$2,$3,$4,now())"
            " ON CONFLICT (account_id) DO UPDATE SET username = EXCLUDED.username, role = EXCLUDED.role,"
            " status = EXCLUDED.status, updated_at = now()",
            aid, str(data.get("username") or ""), str(data.get("role") or ""), str(data.get("status") or ""))
        return True
    if etype == "account.status_changed":
        aid = _account_id(data)
        await conn.execute(
            "INSERT INTO account_projection (account_id, status) VALUES ($1,$2)"
            " ON CONFLICT (account_id) DO UPDATE SET status = EXCLUDED.status, updated_at = now()",
            aid, str(data.get("status") or ""))
        return True       # suspended vẫn GIỮ dữ liệu; chỉ cập nhật trạng thái
    if etype == "account.role_changed":
        aid = _account_id(data)
        await conn.execute(
            "INSERT INTO account_projection (account_id, role) VALUES ($1,$2)"
            " ON CONFLICT (account_id) DO UPDATE SET role = EXCLUDED.role, updated_at = now()",
            aid, str(data.get("role") or ""))
        return True
    if etype == "account.profile_updated":
        aid = _account_id(data)
        await conn.execute(
            "INSERT INTO account_projection (account_id, username) VALUES ($1,$2)"
            " ON CONFLICT (account_id) DO UPDATE SET username = EXCLUDED.username, updated_at = now()",
            aid, str(data.get("username") or ""))
        return True
    if etype == "account.deleted":
        aid = _account_id(data)
        await conn.execute(
            "INSERT INTO account_projection (account_id, deleted) VALUES ($1, true)"
            " ON CONFLICT (account_id) DO UPDATE SET deleted = true, updated_at = now()", aid)
        return True       # ẩn dữ liệu owner khỏi so sánh người
    return False          # loại event khác: không việc gì phải làm


def validate_integration_event(event: dict) -> None:
    data = _data(event)
    if event.get("type") == "snapshot.committed":
        sid = data.get("snapshot_id")
        if not isinstance(sid, str) or not sid.strip():
            raise EventInvalid("snapshot.committed thiếu `snapshot_id`")
