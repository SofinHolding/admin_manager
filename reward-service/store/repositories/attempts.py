"""Bảng `reward_attempts` — mỗi lần thử gửi 1 item. Bất biến 5: `UNIQUE(nonce)` (`ux_reward_attempts_nonce`)."""

from __future__ import annotations

from typing import Any

import asyncpg


async def mark_sent(
    conn: asyncpg.Connection, *, attempt_id: int, command_text: str | None,
    message_id: str | None, http_status: int | None, posted_at: Any = None,
) -> None:
    await conn.execute(
        "UPDATE reward_attempts SET phase='sent', command_text=$1, message_id=$2, "
        "http_status=$3, posted_at=COALESCE($4, now()) WHERE id=$5",
        command_text, message_id, http_status, posted_at, attempt_id,
    )


async def finalize(
    conn: asyncpg.Connection, *, attempt_id: int, phase: str, command_text: str | None = None,
    http_status: int | None = None, reply_message_id: str | None = None,
    reply_author_id: str | None = None, reply_excerpt: str | None = None,
    link_mode: str | None = None, error_code: str | None = None, error_message: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE reward_attempts SET
            phase = $1,
            command_text = COALESCE($2, command_text),
            http_status = COALESCE($3, http_status),
            reply_message_id = $4, reply_author_id = $5, reply_excerpt = $6, link_mode = $7,
            error_code = $8, error_message = $9, finished_at = now()
        WHERE id = $10
        """,
        phase, command_text, http_status, reply_message_id, reply_author_id,
        reply_excerpt, link_mode, error_code, error_message, attempt_id,
    )


async def orphaned(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch("SELECT * FROM reward_attempts WHERE phase IN ('prepared','sent')")
    return [dict(r) for r in rows]


async def get_by_id(conn: asyncpg.Connection, attempt_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM reward_attempts WHERE id=$1", attempt_id)
    return dict(row) if row else None


async def list_by_item(conn: asyncpg.Connection, item_id: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT * FROM reward_attempts WHERE item_id=$1 ORDER BY attempt_no", item_id)
    return [dict(r) for r in rows]
