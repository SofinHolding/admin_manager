"""Bảng `reward_user_map` — thay `users.json` của Node (`src/discord/users-file.js`).

Tra cứu theo `(account_id, normalized_username)` — `normalized_username` đã hạ thường ở tầng gọi
(`domain/parser.py:normalize_username`) nên so khớp coi như case-insensitive.
"""

from __future__ import annotations

import asyncpg


async def lookup(conn: asyncpg.Connection, *, account_id: str, normalized_username: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT discord_user_id FROM reward_user_map WHERE account_id=$1 AND normalized_username=$2",
        account_id, normalized_username,
    )
    return row["discord_user_id"] if row else None


async def save(
    conn: asyncpg.Connection, *, account_id: str, normalized_username: str,
    discord_user_id: str, source: str = "member_search",
) -> None:
    await conn.execute(
        "INSERT INTO reward_user_map (account_id, normalized_username, discord_user_id, source, updated_at) "
        "VALUES ($1,$2,$3,$4, now()) "
        "ON CONFLICT (account_id, normalized_username) DO UPDATE SET "
        "discord_user_id=EXCLUDED.discord_user_id, source=EXCLUDED.source, updated_at=now()",
        account_id, normalized_username, discord_user_id, source,
    )
