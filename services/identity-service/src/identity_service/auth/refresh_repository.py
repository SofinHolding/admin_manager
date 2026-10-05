"""SQL cho bảng `refresh_tokens`. Chỉ lưu hash của token."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def create(conn: asyncpg.Connection, *, token_hash: str, account_id: str, now: datetime,
                 expires_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO refresh_tokens (token_hash, account_id, created_at, expires_at) "
        "VALUES ($1, $2, $3, $4)", token_hash, account_id, now, expires_at)


async def get_valid(conn: asyncpg.Connection, token_hash: str, now: datetime) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM refresh_tokens WHERE token_hash = $1 AND NOT revoked AND expires_at > $2",
        token_hash, now)
    return dict(row) if row else None


async def revoke(conn: asyncpg.Connection, token_hash: str) -> None:
    await conn.execute("UPDATE refresh_tokens SET revoked = true WHERE token_hash = $1", token_hash)


async def revoke_all(conn: asyncpg.Connection, account_id: str) -> None:
    await conn.execute("UPDATE refresh_tokens SET revoked = true WHERE account_id = $1", account_id)
