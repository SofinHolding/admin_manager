"""Platform Registry và Metric Registry — DỮ LIỆU (seed bằng migration), thêm dòng không cần đổi code."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def enabled_platforms(conn: asyncpg.Connection) -> set[str]:
    return {r["key"] for r in await conn.fetch("SELECT key FROM platform_registry WHERE enabled")}


async def enabled_metrics(conn: asyncpg.Connection) -> set[str]:
    return {r["key"] for r in await conn.fetch("SELECT key FROM metric_registry WHERE enabled")}


async def list_platforms(conn: asyncpg.Connection) -> list[dict]:
    return [dict(r) for r in await conn.fetch("SELECT * FROM platform_registry ORDER BY key")]


async def list_metrics(conn: asyncpg.Connection) -> list[dict]:
    return [dict(r) for r in await conn.fetch("SELECT * FROM metric_registry ORDER BY key")]


async def upsert_platform(conn: asyncpg.Connection, key: str, display_name: str, enabled: bool, now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO platform_registry (key, display_name, enabled, created_at, updated_at) "
        "VALUES ($1, $2, $3, $4, $4) ON CONFLICT (key) DO UPDATE SET display_name = EXCLUDED.display_name, "
        "enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at RETURNING *", key, display_name, enabled, now)
    return dict(row)


async def upsert_metric(conn: asyncpg.Connection, key: str, display_name: str, description: str, enabled: bool,
                        now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO metric_registry (key, display_name, description, enabled, created_at, updated_at) "
        "VALUES ($1, $2, $3, $4, $5, $5) ON CONFLICT (key) DO UPDATE SET display_name = EXCLUDED.display_name, "
        "description = EXCLUDED.description, enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at "
        "RETURNING *", key, display_name, description, enabled, now)
    return dict(row)
