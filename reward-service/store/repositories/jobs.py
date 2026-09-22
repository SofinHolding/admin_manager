"""Bảng `reward_jobs`."""

from __future__ import annotations

from typing import Any

import asyncpg


async def create_job(
    conn: asyncpg.Connection, *, account_id: str, name: str, status: str,
    guild_id: str, channel_id: str, command_name: str,
    confirm_mode: str = "reply", success_pattern: str | None = None,
    failure_pattern: str | None = None, leveling_bot_id: str | None = None,
    delay_ms: int = 3000, jitter_ms: int = 500, max_item_retries: int = 3,
    unknown_pause_threshold: int = 5, total_items: int = 0,
    application_id: str | None = None, command_id: str | None = None,
    command_version: str | None = None,
    member_option_name: str = "member", member_option_type: int = 6,
    amount_option_name: str = "amount", amount_option_type: int = 4,
    source_name: str | None = None, source_hash: str | None = None,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO reward_jobs
            (account_id, name, status, guild_id, channel_id, command_name, application_id, command_id,
             command_version, member_option_name, member_option_type, amount_option_name, amount_option_type,
             confirm_mode, success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms,
             max_item_retries, unknown_pause_threshold, total_items, source_name, source_hash)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24)
        RETURNING id
        """,
        account_id, name, status, guild_id, channel_id, command_name, application_id, command_id,
        command_version, member_option_name, member_option_type, amount_option_name, amount_option_type,
        confirm_mode, success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms,
        max_item_retries, unknown_pause_threshold, total_items, source_name, source_hash,
    )
    return row["id"]


async def get_job(conn: asyncpg.Connection, job_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM reward_jobs WHERE id=$1", job_id)
    return dict(row) if row else None


async def set_status(
    conn: asyncpg.Connection, job_id: int, status: str, *,
    started_at: Any = None, finished_at: Any = None, validated_at: Any = None,
) -> None:
    sets = ["status=$1"]
    vals: list[Any] = [status]
    for name, value in (("started_at", started_at), ("finished_at", finished_at), ("validated_at", validated_at)):
        if value is not None:
            vals.append(value)
            sets.append(f"{name}=${len(vals)}")
    vals.append(job_id)
    await conn.execute(f"UPDATE reward_jobs SET {', '.join(sets)} WHERE id=${len(vals)}", *vals)


async def set_totals(conn: asyncpg.Connection, job_id: int, total: int) -> None:
    await conn.execute("UPDATE reward_jobs SET total_items=$1 WHERE id=$2", total, job_id)

async def list_jobs(
    conn: asyncpg.Connection, *, account_id: str | None, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """`account_id=None` ⇒ mọi job (chỉ dùng cho admin) — nếu không phải admin PHẢI truyền account_id."""
    clauses: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        params.append(account_id)
        clauses.append(f"j.account_id = ${len(params)}")
    if status:
        params.append(status)
        clauses.append(f"j.status = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = await conn.fetchval(f"SELECT COUNT(*) FROM reward_jobs j {where}", *params)
    params_page = [*params, limit, offset]
    rows = await conn.fetch(
        f"""
        SELECT j.id, j.name, j.status, j.total_items, j.account_id, j.created_at, j.started_at,
               j.finished_at, a.username AS owner_username
        FROM reward_jobs j
        LEFT JOIN accounts a ON a.id = j.account_id
        {where}
        ORDER BY j.created_at DESC
        LIMIT ${len(params_page) - 1} OFFSET ${len(params_page)}
        """,
        *params_page,
    )
    return [dict(r) for r in rows], int(total)


async def delete_job(conn: asyncpg.Connection, job_id: int) -> None:
    await conn.execute("DELETE FROM reward_jobs WHERE id=$1", job_id)


async def insert_issues(
    conn: asyncpg.Connection, job_id: int,
    issues: list[tuple[int | None, str, str, str]],
) -> None:
    """`issues`: `[(row_index, severity, code, message), ...]`."""
    if not issues:
        return
    await conn.executemany(
        "INSERT INTO reward_validation_issues (job_id, row_index, severity, code, message) "
        "VALUES ($1,$2,$3,$4,$5)",
        [(job_id, row_index, severity, code, message) for row_index, severity, code, message in issues],
    )


async def clear_issues(conn: asyncpg.Connection, job_id: int) -> None:
    await conn.execute("DELETE FROM reward_validation_issues WHERE job_id=$1", job_id)


async def list_issues(conn: asyncpg.Connection, job_id: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT id, row_index, severity, code, message FROM reward_validation_issues "
        "WHERE job_id=$1 ORDER BY row_index NULLS FIRST, id", job_id)
    return [dict(r) for r in rows]


async def clear_items(conn: asyncpg.Connection, job_id: int) -> None:
    await conn.execute("DELETE FROM reward_items WHERE job_id=$1", job_id)
