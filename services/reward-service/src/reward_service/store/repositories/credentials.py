"""Bảng `reward_discord_credentials` — CẤU HÌNH Discord của từng account (1 account = 1 bộ, PK).

Token Discord KHÔNG nằm ở đây nữa — xem `store/repositories/tokens.py` (1 account = N token, xoay
vòng khi chạy job). `status` ở đây = cấu hình (guild + lệnh slash) đã được xác thực bằng ít nhất 1
token hợp lệ hay chưa.
"""

from __future__ import annotations

from typing import Any

import asyncpg

_COLUMNS = (
    "account_id, guild_id, channel_id, command_name, "
    "confirm_mode, success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms, "
    "status, last_error, verified_at, created_at, updated_at"
)


async def get_by_account(conn: asyncpg.Connection, account_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM reward_discord_credentials WHERE account_id = $1", account_id)
    return dict(row) if row else None


async def upsert(
    conn: asyncpg.Connection, *, account_id: str,
    guild_id: str, channel_id: str, command_name: str, confirm_mode: str,
    success_pattern: str | None, failure_pattern: str | None, leveling_bot_id: str | None,
    delay_ms: int, jitter_ms: int, status: str, last_error: str | None,
    set_verified: bool,
) -> dict[str, Any]:
    """UPSERT cấu hình (không đụng token)."""
    row = await conn.fetchrow(
        f"""
        INSERT INTO reward_discord_credentials
            (account_id, guild_id, channel_id, command_name, confirm_mode, success_pattern,
             failure_pattern, leveling_bot_id, delay_ms, jitter_ms, status, last_error,
             verified_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                CASE WHEN $13 THEN now() ELSE NULL END, now())
        ON CONFLICT (account_id) DO UPDATE SET
            guild_id = $2, channel_id = $3, command_name = $4, confirm_mode = $5,
            success_pattern = $6, failure_pattern = $7, leveling_bot_id = $8,
            delay_ms = $9, jitter_ms = $10, status = $11, last_error = $12,
            verified_at = CASE WHEN $13 THEN now() ELSE reward_discord_credentials.verified_at END,
            updated_at = now()
        RETURNING {_COLUMNS}
        """,
        account_id, guild_id, channel_id, command_name, confirm_mode, success_pattern,
        failure_pattern, leveling_bot_id, delay_ms, jitter_ms, status, last_error, set_verified,
    )
    return dict(row)


async def set_status(
    conn: asyncpg.Connection, *, account_id: str, status: str, last_error: str | None,
    set_verified: bool = False,
) -> None:
    await conn.execute(
        "UPDATE reward_discord_credentials SET status=$1, last_error=$2, updated_at=now(), "
        "verified_at = CASE WHEN $4 THEN now() ELSE verified_at END "
        "WHERE account_id=$3",
        status, last_error, account_id, set_verified,
    )


async def delete(conn: asyncpg.Connection, account_id: str) -> None:
    await conn.execute("DELETE FROM reward_discord_credentials WHERE account_id = $1", account_id)
