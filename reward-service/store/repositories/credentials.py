"""Bảng `reward_discord_credentials` — thông tin Discord của từng account (1 account = 1 bộ, PK).

`token_ciphertext` là Fernet(user token) — KHÔNG BAO GIỜ trả qua API, kể cả dạng che. `decrypt_token`
CHỈ dùng nội bộ (bởi `api/credentials.py:verify` và `engine/worker_manager.py`), KHÔNG BAO GIỜ được
gọi từ một endpoint trả response trực tiếp ra ngoài.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from crypto import TokenCrypto

_PUBLIC_COLUMNS = (
    "account_id, discord_user_id, discord_username, guild_id, channel_id, command_name, "
    "confirm_mode, success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms, "
    "status, last_error, verified_at, created_at, updated_at"
)


async def get_by_account(conn: asyncpg.Connection, account_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"SELECT {_PUBLIC_COLUMNS} FROM reward_discord_credentials WHERE account_id = $1", account_id)
    return dict(row) if row else None


async def get_raw(conn: asyncpg.Connection, account_id: str) -> dict[str, Any] | None:
    """Bao gồm `token_ciphertext` — CHỈ dùng nội bộ, KHÔNG BAO GIỜ trả trực tiếp ra API."""
    row = await conn.fetchrow(
        "SELECT * FROM reward_discord_credentials WHERE account_id = $1", account_id)
    return dict(row) if row else None


async def upsert(
    conn: asyncpg.Connection, *, account_id: str,
    token_ciphertext: str | None, discord_user_id: str | None, discord_username: str | None,
    guild_id: str, channel_id: str, command_name: str, confirm_mode: str,
    success_pattern: str | None, failure_pattern: str | None, leveling_bot_id: str | None,
    delay_ms: int, jitter_ms: int, status: str, last_error: str | None,
    set_verified: bool,
) -> dict[str, Any]:
    """UPSERT cấu hình + (tuỳ chọn) `token_ciphertext`.

    `token_ciphertext=None` ⇒ giữ nguyên ciphertext hiện có (không ghi đè bằng NULL) — đúng nghĩa
    PUT `token` bỏ trống chỉ cập nhật cấu hình (plan C.2)."""
    row = await conn.fetchrow(
        f"""
        INSERT INTO reward_discord_credentials
            (account_id, token_ciphertext, discord_user_id, discord_username, guild_id, channel_id,
             command_name, confirm_mode, success_pattern, failure_pattern, leveling_bot_id,
             delay_ms, jitter_ms, status, last_error, verified_at, updated_at)
        VALUES ($1, COALESCE($2, ''), $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                CASE WHEN $16 THEN now() ELSE NULL END, now())
        ON CONFLICT (account_id) DO UPDATE SET
            token_ciphertext = COALESCE($2, reward_discord_credentials.token_ciphertext),
            discord_user_id = COALESCE($3, reward_discord_credentials.discord_user_id),
            discord_username = COALESCE($4, reward_discord_credentials.discord_username),
            guild_id = $5, channel_id = $6, command_name = $7, confirm_mode = $8,
            success_pattern = $9, failure_pattern = $10, leveling_bot_id = $11,
            delay_ms = $12, jitter_ms = $13, status = $14, last_error = $15,
            verified_at = CASE WHEN $16 THEN now() ELSE reward_discord_credentials.verified_at END,
            updated_at = now()
        RETURNING {_PUBLIC_COLUMNS}
        """,
        account_id, token_ciphertext, discord_user_id, discord_username, guild_id, channel_id,
        command_name, confirm_mode, success_pattern, failure_pattern, leveling_bot_id,
        delay_ms, jitter_ms, status, last_error, set_verified,
    )
    return dict(row)


async def set_status(
    conn: asyncpg.Connection, *, account_id: str, status: str, last_error: str | None,
) -> None:
    await conn.execute(
        "UPDATE reward_discord_credentials SET status=$1, last_error=$2, updated_at=now() "
        "WHERE account_id=$3",
        status, last_error, account_id,
    )


async def delete(conn: asyncpg.Connection, account_id: str) -> None:
    await conn.execute("DELETE FROM reward_discord_credentials WHERE account_id = $1", account_id)


async def decrypt_token(conn: asyncpg.Connection, *, account_id: str, crypto: TokenCrypto) -> str | None:
    """Giải mã token Discord của account — CHỈ dùng nội bộ (verify / worker lúc start job)."""
    row = await conn.fetchrow(
        "SELECT token_ciphertext FROM reward_discord_credentials WHERE account_id = $1", account_id)
    if row is None or not row["token_ciphertext"]:
        return None
    return crypto.decrypt(row["token_ciphertext"])
