"""SQL cho `permissions` (catalog) và `account_permissions` (grant)."""

from __future__ import annotations

import asyncpg


async def catalog(conn: asyncpg.Connection) -> list[dict]:
    return [dict(r) for r in await conn.fetch("SELECT * FROM permissions ORDER BY name")]


async def get(conn: asyncpg.Connection, name: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM permissions WHERE name = $1", name)
    return dict(row) if row else None


async def granted(conn: asyncpg.Connection, account_id: str) -> list[str]:
    return [r["permission"] for r in await conn.fetch(
        "SELECT permission FROM account_permissions WHERE account_id = $1 ORDER BY permission", account_id)]


async def grant(conn: asyncpg.Connection, account_id: str, permission: str, by: str) -> bool:
    """True nếu vừa cấp mới (False: đã có sẵn)."""
    tag = await conn.execute(
        "INSERT INTO account_permissions (account_id, permission, granted_by) VALUES ($1, $2, $3) "
        "ON CONFLICT DO NOTHING", account_id, permission, by)
    return tag.endswith(" 1")


async def revoke(conn: asyncpg.Connection, account_id: str, permission: str) -> bool:
    tag = await conn.execute("DELETE FROM account_permissions WHERE account_id = $1 AND permission = $2",
                             account_id, permission)
    return tag.endswith(" 1")
