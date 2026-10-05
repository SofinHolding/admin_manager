"""SQL cho bảng `sessions`. Thu hồi phiên làm access token mang `sid` đó bị từ chối ở những nơi kiểm tra phiên."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def create(conn: asyncpg.Connection, *, sid: str, account_id: str, client_type: str, now: datetime) -> None:
    await conn.execute("INSERT INTO sessions (sid, account_id, client_type, created_at) VALUES ($1, $2, $3, $4)",
                       sid, account_id, client_type, now)


async def is_active(conn: asyncpg.Connection, sid: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM sessions WHERE sid = $1 AND revoked_at IS NULL)", sid))


async def revoke(conn: asyncpg.Connection, sid: str, now: datetime) -> None:
    await conn.execute("UPDATE sessions SET revoked_at = $2 WHERE sid = $1 AND revoked_at IS NULL", sid, now)


async def revoke_all(conn: asyncpg.Connection, account_id: str, now: datetime) -> int:
    tag = await conn.execute("UPDATE sessions SET revoked_at = $2 WHERE account_id = $1 AND revoked_at IS NULL",
                             account_id, now)
    return int(tag.rsplit(" ", 1)[-1])


async def revoke_if_active(conn: asyncpg.Connection, sid: str, now: datetime) -> bool:
    """Thu hồi phiên; True nếu phiên TỒN TẠI và vừa chuyển từ sống sang bị thu hồi (để phát event đúng một lần)."""
    return bool(await conn.fetchval(
        "UPDATE sessions SET revoked_at = $2 WHERE sid = $1 AND revoked_at IS NULL RETURNING 1", sid, now))
