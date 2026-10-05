"""Transactional outbox + cấp `revision` canonical của integration-service.

QUAN TRỌNG (đúng thứ tự): `pg_advisory_xact_lock` lấy TRƯỚC khi cấp `seq` (outbox) hoặc `revision` (canonical) và giữ tới
hết transaction. Nhờ vậy transaction sau không thể có `seq`/`revision` nhỏ hơn mà commit muộn hơn; consumer đọc theo con
trỏ không bao giờ bỏ lỡ bản ghi. Đánh đổi: các transaction ghi canonical/outbox được tuần tự hoá (chấp nhận ở quy mô này).
"""

from __future__ import annotations

import asyncpg

from integration_service.security import new_id

OUTBOX_LOCK = 7_350_002  # khoá advisory riêng của outbox + revision canonical của integration-service


async def acquire_order_lock(conn: asyncpg.Connection) -> None:
    """Tuần tự hoá việc cấp seq/revision theo thứ tự commit. Gọi lại trong cùng transaction là no-op."""
    await conn.execute("SELECT pg_advisory_xact_lock($1)", OUTBOX_LOCK)


async def next_revisions(conn: asyncpg.Connection, count: int) -> list[int]:
    """Cấp `count` revision liên tiếp (dưới khoá thứ tự commit)."""
    if count <= 0:
        return []
    await acquire_order_lock(conn)
    rows = await conn.fetch("SELECT nextval('canonical_revision_seq') AS r FROM generate_series(1, $1)", count)
    return [r["r"] for r in rows]


async def head_revision(conn: asyncpg.Connection) -> int:
    row = await conn.fetchrow("SELECT last_value, is_called FROM canonical_revision_seq")
    return int(row["last_value"]) if row["is_called"] else 0


async def append(conn: asyncpg.Connection, *, type: str, subject: str, data: dict, data_version: int = 1) -> int:
    await acquire_order_lock(conn)
    return await conn.fetchval(
        "INSERT INTO outbox (id, type, subject, data_version, data) VALUES ($1, $2, $3, $4, $5) "
        "RETURNING seq", new_id("evt"), type, subject, data_version, data)


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
