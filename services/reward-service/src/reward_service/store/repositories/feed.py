"""Sổ sách consumer feed: `feed_cursor`, `processed_events`, `dead_letters` (internal-feed-contract-v1 §6)."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


async def get_cursor(conn: asyncpg.Connection, source: str) -> int:
    value = await conn.fetchval("SELECT seq FROM feed_cursor WHERE source = $1", source)
    return int(value or 0)


async def set_cursor(conn: asyncpg.Connection, source: str, seq: int) -> None:
    """Con trỏ chỉ tiến, không bao giờ lùi (replay thủ công đặt lại bằng `reset_cursor`)."""
    await conn.execute(
        "INSERT INTO feed_cursor (source, seq) VALUES ($1, $2) "
        "ON CONFLICT (source) DO UPDATE SET seq = GREATEST(feed_cursor.seq, EXCLUDED.seq), updated_at = now()",
        source, seq)


async def reset_cursor(conn: asyncpg.Connection, source: str, seq: int = 0) -> None:
    await conn.execute(
        "INSERT INTO feed_cursor (source, seq) VALUES ($1, $2) "
        "ON CONFLICT (source) DO UPDATE SET seq = EXCLUDED.seq, updated_at = now()", source, seq)


async def is_processed(conn: asyncpg.Connection, event_id: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM processed_events WHERE id = $1", event_id))


async def mark_processed(
    conn: asyncpg.Connection, *, event_id: str, type_: str, subject: str, seq: int | None,
) -> bool:
    """True nếu vừa đánh dấu lần đầu; False nếu đã có (event lặp)."""
    tag = await conn.execute(
        "INSERT INTO processed_events (id, type, subject, seq) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (id) DO NOTHING", event_id, type_, subject, seq)
    return tag.endswith(" 1")


async def superseded(conn: asyncpg.Connection, *, type_: str, subject: str, seq: int) -> bool:
    """Đã xử lý một event CÙNG loại + chủ thể có `seq` lớn hơn? (replay dead-letter cũ không được ghi đè trạng thái mới)."""
    return bool(await conn.fetchval(
        "SELECT 1 FROM processed_events WHERE type = $1 AND subject = $2 AND seq > $3 LIMIT 1",
        type_, subject, seq))


async def add_dead_letter(
    conn: asyncpg.Connection, *, event: dict[str, Any], error: str, attempts: int,
) -> None:
    await conn.execute(
        """
        INSERT INTO dead_letters (id, type, error, attempts, payload, seq)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        ON CONFLICT (id) DO UPDATE SET error = EXCLUDED.error, attempts = EXCLUDED.attempts,
                                       replayed_at = NULL
        """,
        str(event.get("id")), str(event.get("type")), error[:500], attempts, json.dumps(event), event.get("seq"))


async def pending_dead_letters(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT id, type, error, attempts, payload, seq, created_at FROM dead_letters "
        "WHERE replayed_at IS NULL ORDER BY seq NULLS LAST, created_at")
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["payload"], str):
            d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


async def mark_replayed(conn: asyncpg.Connection, event_id: str) -> None:
    await conn.execute("UPDATE dead_letters SET replayed_at = now() WHERE id = $1", event_id)
