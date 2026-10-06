"""Bảng `reward_discord_tokens` — N token Discord / account, dùng xoay vòng khi chạy job.

`token_ciphertext` là Fernet(user token) — KHÔNG BAO GIỜ trả qua API, kể cả dạng che. `list_usable`
(có ciphertext) CHỈ dùng nội bộ (bởi `api/credentials.py` lúc xác thực và `engine/worker_manager.py`
lúc khởi động job), KHÔNG BAO GIỜ được gọi từ endpoint trả response trực tiếp ra ngoài.
"""

from __future__ import annotations

from typing import Any

import asyncpg

_PUBLIC_COLUMNS = (
    "id, account_id, label, discord_user_id, discord_username, status, enabled, last_error, "
    "verified_at, last_used_at, created_at, updated_at"
)


async def list_public(conn: asyncpg.Connection, account_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        f"SELECT {_PUBLIC_COLUMNS} FROM reward_discord_tokens WHERE account_id = $1 ORDER BY id", account_id)
    return [dict(r) for r in rows]


async def get_public(conn: asyncpg.Connection, account_id: str, token_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"SELECT {_PUBLIC_COLUMNS} FROM reward_discord_tokens WHERE account_id = $1 AND id = $2",
        account_id, token_id)
    return dict(row) if row else None


async def list_raw(conn: asyncpg.Connection, account_id: str) -> list[dict[str, Any]]:
    """Bao gồm `token_ciphertext` — CHỈ dùng nội bộ, KHÔNG BAO GIỜ trả trực tiếp ra API."""
    rows = await conn.fetch(
        "SELECT * FROM reward_discord_tokens WHERE account_id = $1 ORDER BY id", account_id)
    return [dict(r) for r in rows]


async def list_usable(conn: asyncpg.Connection, account_id: str) -> list[dict[str, Any]]:
    """Token tham gia xoay vòng: `enabled` + `status='valid'` (kèm ciphertext, chỉ dùng nội bộ)."""
    rows = await conn.fetch(
        "SELECT * FROM reward_discord_tokens WHERE account_id = $1 AND enabled AND status = 'valid' ORDER BY id",
        account_id)
    return [dict(r) for r in rows]


async def count_usable(conn: asyncpg.Connection, account_id: str) -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM reward_discord_tokens WHERE account_id = $1 AND enabled AND status = 'valid'",
        account_id))


async def insert(
    conn: asyncpg.Connection, *, account_id: str, label: str, token_ciphertext: str,
    discord_user_id: str | None, discord_username: str | None, status: str, last_error: str | None,
    set_verified: bool,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"""
        INSERT INTO reward_discord_tokens
            (account_id, label, token_ciphertext, discord_user_id, discord_username, status,
             last_error, verified_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, CASE WHEN $8 THEN now() ELSE NULL END)
        RETURNING {_PUBLIC_COLUMNS}
        """,
        account_id, label, token_ciphertext, discord_user_id, discord_username, status,
        last_error, set_verified)
    return dict(row)


async def find_by_discord_user(
    conn: asyncpg.Connection, account_id: str, discord_user_id: str,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"SELECT {_PUBLIC_COLUMNS} FROM reward_discord_tokens "
        "WHERE account_id = $1 AND discord_user_id = $2", account_id, discord_user_id)
    return dict(row) if row else None


async def update_meta(
    conn: asyncpg.Connection, *, account_id: str, token_id: int, label: str | None, enabled: bool | None,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"""
        UPDATE reward_discord_tokens SET
            label = COALESCE($3, label), enabled = COALESCE($4, enabled), updated_at = now()
        WHERE account_id = $1 AND id = $2
        RETURNING {_PUBLIC_COLUMNS}
        """,
        account_id, token_id, label, enabled)
    return dict(row) if row else None


async def set_verification(
    conn: asyncpg.Connection, *, account_id: str, token_id: int, status: str, last_error: str | None,
    discord_user_id: str | None = None, discord_username: str | None = None,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"""
        UPDATE reward_discord_tokens SET
            status = $3, last_error = $4,
            discord_user_id = COALESCE($5, discord_user_id),
            discord_username = COALESCE($6, discord_username),
            verified_at = CASE WHEN $3 = 'valid' THEN now() ELSE verified_at END,
            updated_at = now()
        WHERE account_id = $1 AND id = $2
        RETURNING {_PUBLIC_COLUMNS}
        """,
        account_id, token_id, status, last_error, discord_user_id, discord_username)
    return dict(row) if row else None


async def mark_invalid(conn: asyncpg.Connection, *, token_id: int, last_error: str) -> None:
    """Engine đánh dấu token hỏng giữa lúc chạy job (401/403/404) — không cần `account_id`."""
    await conn.execute(
        "UPDATE reward_discord_tokens SET status='invalid', last_error=$2, updated_at=now() WHERE id=$1",
        token_id, last_error)


async def touch_used(conn: asyncpg.Connection, token_id: int) -> None:
    await conn.execute("UPDATE reward_discord_tokens SET last_used_at=now() WHERE id=$1", token_id)


async def delete(conn: asyncpg.Connection, *, account_id: str, token_id: int) -> bool:
    res = await conn.execute(
        "DELETE FROM reward_discord_tokens WHERE account_id = $1 AND id = $2", account_id, token_id)
    return res.endswith(" 1")


async def delete_all(conn: asyncpg.Connection, account_id: str) -> None:
    await conn.execute("DELETE FROM reward_discord_tokens WHERE account_id = $1", account_id)
