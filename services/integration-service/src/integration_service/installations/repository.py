"""SQL của installations (một instance/thiết bị của Integration Client)."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def upsert(conn: asyncpg.Connection, *, new_id: str, client_id: str, device_id: str, hostname: str, os: str,
                 app_version: str, capabilities: list[str], ip: str, now: datetime) -> tuple[dict, bool]:
    """(bản ghi, created). Cùng (client_id, device_id) => cập nhật, không sinh installation mới."""
    row = await conn.fetchrow(
        "INSERT INTO installations (id, client_id, device_id, hostname, os, app_version, capabilities, first_seen_at, "
        "last_seen_at, last_ip) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$8,$9) "
        "ON CONFLICT (client_id, device_id) DO UPDATE SET hostname = EXCLUDED.hostname, os = EXCLUDED.os, "
        "app_version = EXCLUDED.app_version, capabilities = EXCLUDED.capabilities, last_seen_at = EXCLUDED.last_seen_at, "
        "last_ip = EXCLUDED.last_ip RETURNING *, (xmax = 0) AS created",
        new_id, client_id, device_id, hostname, os, app_version, capabilities, now, ip)
    out = dict(row)
    return out, out.pop("created")


async def list_for_client(conn: asyncpg.Connection, client_id: str) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM installations WHERE client_id = $1 ORDER BY first_seen_at, id", client_id)
    return [dict(r) for r in rows]


async def exists_for_client(conn: asyncpg.Connection, installation_id: str, client_id: str) -> bool:
    return await conn.fetchval("SELECT EXISTS (SELECT 1 FROM installations WHERE id = $1 AND client_id = $2)",
                               installation_id, client_id)
