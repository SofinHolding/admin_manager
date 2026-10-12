"""SQL của channel_groups (nhóm quốc gia + kênh do user tự quản lý, phạm vi theo `owner_account_id`)."""

from __future__ import annotations

import asyncpg


def _row(row: asyncpg.Record) -> dict:
    """`id` là UUID của Postgres; ép về `str` để JSON hoá được qua envelope response."""
    d = dict(row)
    d["id"] = str(d["id"])
    return d


async def list_by_owner(conn: asyncpg.Connection, owner_id: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM user_channel_groups WHERE owner_account_id = $1 ORDER BY sort_order, created_at", owner_id)
    return [_row(r) for r in rows]


async def get(conn: asyncpg.Connection, group_id: str, owner_id: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM user_channel_groups WHERE id = $1 AND owner_account_id = $2", group_id, owner_id)
    return _row(row) if row else None


async def create(conn: asyncpg.Connection, owner_id: str, name: str, country_code: str) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO user_channel_groups (owner_account_id, name, country_code) VALUES ($1, $2, $3) RETURNING *",
        owner_id, name, country_code)
    return _row(row)


async def update(conn: asyncpg.Connection, group_id: str, owner_id: str, **fields: object) -> dict | None:
    if not fields:
        return await get(conn, group_id, owner_id)
    sets = ", ".join(f"{key} = ${i}" for i, key in enumerate(fields, start=3))
    row = await conn.fetchrow(
        f"UPDATE user_channel_groups SET {sets} WHERE id = $1 AND owner_account_id = $2 RETURNING *",
        group_id, owner_id, *fields.values())
    return _row(row) if row else None


async def delete(conn: asyncpg.Connection, group_id: str, owner_id: str) -> bool:
    result = await conn.execute(
        "DELETE FROM user_channel_groups WHERE id = $1 AND owner_account_id = $2", group_id, owner_id)
    return result.endswith(" 1")
