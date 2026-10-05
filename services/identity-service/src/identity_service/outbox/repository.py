"""Transactional outbox của identity-service.

Ghi trong CÙNG transaction với thay đổi nghiệp vụ. `pg_advisory_xact_lock` lấy TRƯỚC `nextval` và giữ tới commit để
thứ tự `seq` = thứ tự commit: consumer đọc theo con trỏ không bao giờ bỏ lỡ sự kiện commit muộn có seq nhỏ hơn.
"""

from __future__ import annotations

import json
import uuid

import asyncpg

OUTBOX_LOCK = 7_340_002  # khoá advisory riêng của outbox identity


async def append(conn: asyncpg.Connection, *, type: str, subject: str, data: dict, data_version: int = 1) -> int:
    await conn.execute("SELECT pg_advisory_xact_lock($1)", OUTBOX_LOCK)
    return await conn.fetchval(
        "INSERT INTO outbox (id, type, subject, data_version, data) VALUES ($1, $2, $3, $4, $5::jsonb) "
        "RETURNING seq", f"evt_{uuid.uuid4().hex}", type, subject, data_version,
        json.dumps(data, ensure_ascii=False))


async def read(conn: asyncpg.Connection, *, after: int, limit: int, types: list[str] | None = None) -> list[dict]:
    if types:
        rows = await conn.fetch(
            "SELECT * FROM outbox WHERE seq > $1 AND type = ANY($3::text[]) ORDER BY seq LIMIT $2",
            after, limit, types)
    else:
        rows = await conn.fetch("SELECT * FROM outbox WHERE seq > $1 ORDER BY seq LIMIT $2", after, limit)
    return [dict(r) for r in rows]


async def head(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT COALESCE(max(seq), 0) FROM outbox")
