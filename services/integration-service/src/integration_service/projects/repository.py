"""SQL của projects (namespace chủ đề/nhóm, tự upsert theo `project_key` trong phạm vi owner)."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def get(conn: asyncpg.Connection, owner: str, project_key: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM projects WHERE owner_account_id = $1 AND project_key = $2", owner,
                              project_key)
    return dict(row) if row else None


async def insert(conn: asyncpg.Connection, *, id: str, owner: str, client_id: str, project_key: str,
                 platform: str | None, name: str, country_code: str | None, enabled: bool, metadata: dict,
                 revision: int, now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO projects (id, owner_account_id, client_id, project_key, platform, name, country_code, enabled, "
        "metadata, revision, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11) RETURNING *",
        id, owner, client_id, project_key, platform, name, country_code, enabled, metadata, revision, now)
    return dict(row)


async def update(conn: asyncpg.Connection, project_id: str, *, client_id: str, platform: str | None, name: str,
                 country_code: str | None, enabled: bool, metadata: dict, revision: int, now: datetime) -> dict:
    row = await conn.fetchrow(
        "UPDATE projects SET client_id = $2, platform = $3, name = $4, country_code = $5, enabled = $6, metadata = $7, "
        "removed = false, revision = $8, updated_at = $9 WHERE id = $1 RETURNING *",
        project_id, client_id, platform, name, country_code, enabled, metadata, revision, now)
    return dict(row)
