"""Read model tài khoản (`account_projection`) và denylist cục bộ (`account_denylist`).

Cả hai là bảng RIÊNG của Reward, nuôi bằng event từ feed Identity (hoặc bằng thao tác quản trị đã được Identity
chấp nhận). Reward KHÔNG bao giờ đọc/ghi bảng của Identity.

Denylist:
  * `permission_revoked` | `suspended` | `deleted` — chặn MỌI token của tài khoản cho tới khi có event ngược lại
    (`sid` luôn là chuỗi rỗng).
  * `session_revoked` — chặn token có `sid` khớp; `sid` rỗng = mọi phiên: chặn token phát hành (`iat`) trước hoặc
    đúng thời điểm thu hồi (`denied_at`). Token do phiên đăng nhập SAU đó phát hành vẫn hợp lệ.
"""

from __future__ import annotations

import datetime
from typing import Any

import asyncpg

BLANKET_REASONS = ("permission_revoked", "suspended", "deleted")
SESSION_REASON = "session_revoked"
SESSION_ENTRY_TTL_S = 86400  # mục session_revoked già hơn thế không còn token nào sống để chặn (access TTL ≈ 15 phút)


async def upsert_projection(
    conn: asyncpg.Connection, *, account_id: str, username: str | None = None, role: str | None = None,
    status: str | None = None, reward_access: bool | None = None,
) -> None:
    """Chèn mới hoặc cập nhật CHỈ các trường được cung cấp (None = giữ nguyên)."""
    await conn.execute(
        """
        INSERT INTO account_projection (account_id, username, role, status, reward_access)
        VALUES ($1, COALESCE($2, ''), COALESCE($3, ''), COALESCE($4, 'active'), COALESCE($5, false))
        ON CONFLICT (account_id) DO UPDATE SET
            username = COALESCE($2, account_projection.username),
            role = COALESCE($3, account_projection.role),
            status = COALESCE($4, account_projection.status),
            reward_access = COALESCE($5, account_projection.reward_access),
            updated_at = now()
        """,
        account_id, username, role, status, reward_access)


async def projections_by_ids(conn: asyncpg.Connection, ids: list[str]) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT account_id, username, role, status, reward_access FROM account_projection "
        "WHERE account_id = ANY($1::text[])", ids)
    return {r["account_id"]: dict(r) for r in rows}


async def delete_projection(conn: asyncpg.Connection, account_id: str) -> None:
    await conn.execute("DELETE FROM account_projection WHERE account_id = $1", account_id)


async def get_projection(conn: asyncpg.Connection, account_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT account_id, username, role, status FROM account_projection WHERE account_id = $1", account_id)
    return dict(row) if row else None


async def add_denial(
    conn: asyncpg.Connection, *, account_id: str, reason: str, sid: str = "",
    denied_at: datetime.datetime | None = None, event_id: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO account_denylist (account_id, reason, sid, denied_at, event_id)
        VALUES ($1, $2, $3, COALESCE($4, now()), $5)
        ON CONFLICT (account_id, reason, sid) DO UPDATE SET
            denied_at = GREATEST(account_denylist.denied_at, EXCLUDED.denied_at), event_id = EXCLUDED.event_id
        """,
        account_id, reason, sid, denied_at, event_id)


async def lift_denial(conn: asyncpg.Connection, *, account_id: str, reason: str) -> int:
    tag = await conn.execute(
        "DELETE FROM account_denylist WHERE account_id = $1 AND reason = $2", account_id, reason)
    return int(tag.split()[-1])


async def blanket_denial(conn: asyncpg.Connection, account_id: str) -> str | None:
    """Lý do chặn toàn tài khoản (dùng cho engine: không có token để so `sid`/`iat`). None = không bị chặn."""
    return await conn.fetchval(
        "SELECT reason FROM account_denylist WHERE account_id = $1 AND reason = ANY($2::text[]) LIMIT 1",
        account_id, list(BLANKET_REASONS))


async def is_denied(conn: asyncpg.Connection, *, account_id: str, sid: str = "", iat: int = 0) -> str | None:
    """Request-time: trả lý do nếu token (account_id, sid, iat) bị chặn, ngược lại None."""
    return await conn.fetchval(
        """
        SELECT reason FROM account_denylist
        WHERE account_id = $1 AND (
              reason = ANY($2::text[])
           OR (reason = $3 AND (sid = $4 OR (sid = '' AND denied_at >= to_timestamp($5)))))
        LIMIT 1
        """,
        account_id, list(BLANKET_REASONS), SESSION_REASON, sid or "-", float(iat))


async def prune_session_denials(conn: asyncpg.Connection) -> int:
    tag = await conn.execute(
        "DELETE FROM account_denylist WHERE reason = $1 AND denied_at < now() - make_interval(secs => $2)",
        SESSION_REASON, float(SESSION_ENTRY_TTL_S))
    return int(tag.split()[-1])


async def list_denials(conn: asyncpg.Connection, account_id: str | None = None) -> list[dict[str, Any]]:
    if account_id:
        rows = await conn.fetch(
            "SELECT account_id, reason, sid, denied_at FROM account_denylist WHERE account_id = $1 ORDER BY id",
            account_id)
    else:
        rows = await conn.fetch("SELECT account_id, reason, sid, denied_at FROM account_denylist ORDER BY id")
    return [dict(r) for r in rows]
