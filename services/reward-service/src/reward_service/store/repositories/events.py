"""Bảng `reward_job_events` — nhật ký sự kiện job."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


async def add_event(
    conn: asyncpg.Connection, *, job_id: int, type: str, actor: str, payload: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO reward_job_events (job_id, at, type, actor, payload) VALUES ($1, now(), $2, $3, $4::jsonb)",
        job_id, type, actor, json.dumps(payload) if payload is not None else None,
    )


async def list_events(conn: asyncpg.Connection, job_id: int, *, after_id: int | None = None) -> list[dict[str, Any]]:
    if after_id is not None:
        rows = await conn.fetch(
            "SELECT * FROM reward_job_events WHERE job_id=$1 AND id > $2 ORDER BY id", job_id, after_id)
    else:
        rows = await conn.fetch("SELECT * FROM reward_job_events WHERE job_id=$1 ORDER BY id", job_id)
    return [dict(r) for r in rows]
