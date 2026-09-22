"""Bảng `reward_distributions` — sổ cái phân phối. PK = `item_id` (bất biến 3): `insert_once` dựa
vào PRIMARY KEY để tự chặn ghi trùng — KHÔNG `SELECT` kiểm tra trước (đúng semantics Node).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg


async def insert_once(
    conn: asyncpg.Connection, *, item_id: int, attempt_id: int, job_id: int,
    account_id: str, discord_user_id: str, point: int, evidence: dict[str, Any],
) -> None:
    await conn.execute(
        "INSERT INTO reward_distributions "
        "(item_id, attempt_id, job_id, account_id, discord_user_id, point, evidence, distributed_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb, now())",
        item_id, attempt_id, job_id, account_id, discord_user_id, point, json.dumps(evidence),
    )


async def get_by_item(conn: asyncpg.Connection, item_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM reward_distributions WHERE item_id=$1", item_id)
    return dict(row) if row else None


async def count_for_job(conn: asyncpg.Connection, job_id: int) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM reward_distributions WHERE job_id=$1", job_id)
