"""Bảng `reward_items` — trung tâm chống trùng (bất biến 1 & 2, xem plan B.2.3).

`claim_next` gộp SELECT + UPDATE thành MỘT câu `UPDATE ... WHERE id=(SELECT ... FOR UPDATE SKIP
LOCKED LIMIT 1) RETURNING *` rồi `INSERT reward_attempts(phase='prepared')` — write-ahead TRƯỚC khi
gọi Discord (B.3.1). An toàn nhiều worker cùng job nhờ `SKIP LOCKED` (bất biến I6 — mỗi item chỉ
được claim đúng một lần dù có nhiều coroutine/tiến trình tranh nhau).
"""

from __future__ import annotations

from typing import Any

import asyncpg


async def insert_items(conn: asyncpg.Connection, job_id: int, rows: list[dict[str, Any]]) -> None:
    await conn.executemany(
        "INSERT INTO reward_items "
        "(job_id, row_index, raw_username, normalized_username, point, status, idempotency_key) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7)",
        [
            (job_id, r["row_index"], r["raw_username"], r["normalized_username"], r["point"],
             r.get("status", "pending"), r["idempotency_key"])
            for r in rows
        ],
    )


async def claim_next(conn: asyncpg.Connection, job_id: int, *, nonce: str) -> dict[str, Any] | None:
    """Write-ahead claim: 1 transaction gồm UPDATE (FOR UPDATE SKIP LOCKED) + INSERT attempt(prepared).

    Trả `None` nếu hết việc (hoặc mọi ứng viên đã bị coroutine khác chiếm mất nhờ SKIP LOCKED)."""
    async with conn.transaction():
        item_row = await conn.fetchrow(
            """
            UPDATE reward_items SET status='processing', attempt_count = attempt_count + 1, last_attempt_at = now()
            WHERE id = (
                SELECT id FROM reward_items
                WHERE job_id = $1 AND status IN ('pending','retrying')
                ORDER BY row_index
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
            """,
            job_id,
        )
        if item_row is None:
            return None
        item = dict(item_row)
        attempt_no = item["attempt_count"]
        attempt_row = await conn.fetchrow(
            "INSERT INTO reward_attempts (item_id, job_id, attempt_no, nonce, phase, started_at) "
            "VALUES ($1,$2,$3,$4,'prepared', now()) RETURNING id",
            item["id"], job_id, attempt_no, nonce,
        )
        return {"item": item, "attempt": {"id": attempt_row["id"], "attempt_no": attempt_no, "nonce": nonce}}


async def set_status(conn: asyncpg.Connection, item_id: int, status: str) -> None:
    await conn.execute("UPDATE reward_items SET status=$1 WHERE id=$2", status, item_id)


async def finalize(
    conn: asyncpg.Connection, *, item_id: int, status: str, confirmation_level: str | None = None,
    failure_code: str | None = None, failure_message: str | None = None,
    resolved_user_id: str | None = None, resolve_level: str | None = None,
    resolved_by: str | None = None, first_sent_at: Any = None,
) -> None:
    await conn.execute(
        """
        UPDATE reward_items SET
            status = $1, confirmation_level = $2, failure_code = $3, failure_message = $4,
            resolved_user_id = COALESCE($5, resolved_user_id),
            resolve_level = COALESCE($6, resolve_level),
            resolved_by = COALESCE($7, resolved_by),
            first_sent_at = COALESCE(first_sent_at, $8),
            finalized_at = now()
        WHERE id = $9
        """,
        status, confirmation_level, failure_code, failure_message,
        resolved_user_id, resolve_level, resolved_by, first_sent_at, item_id,
    )


async def get_item(conn: asyncpg.Connection, item_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM reward_items WHERE id=$1", item_id)
    return dict(row) if row else None


async def list_by_status(conn: asyncpg.Connection, job_id: int, status: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT * FROM reward_items WHERE job_id=$1 AND status=$2 ORDER BY row_index", job_id, status)
    return [dict(r) for r in rows]


async def list_all(conn: asyncpg.Connection, job_id: int) -> list[dict[str, Any]]:
    rows = await conn.fetch("SELECT * FROM reward_items WHERE job_id=$1 ORDER BY row_index", job_id)
    return [dict(r) for r in rows]


async def list_page(
    conn: asyncpg.Connection, job_id: int, *, status: str | None = None, q: str | None = None,
    limit: int = 50, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["job_id = $1"]
    params: list[Any] = [job_id]
    if status:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    if q:
        params.append(f"%{q.lower()}%")
        clauses.append(f"lower(raw_username) LIKE ${len(params)}")
    where = " AND ".join(clauses)
    total = await conn.fetchval(f"SELECT COUNT(*) FROM reward_items WHERE {where}", *params)
    params_page = [*params, limit, offset]
    rows = await conn.fetch(
        f"SELECT * FROM reward_items WHERE {where} ORDER BY row_index "
        f"LIMIT ${len(params_page) - 1} OFFSET ${len(params_page)}",
        *params_page,
    )
    return [dict(r) for r in rows], int(total)


async def counts(conn: asyncpg.Connection, job_id: int) -> dict[str, int]:
    rows = await conn.fetch(
        "SELECT status, COUNT(*) AS n FROM reward_items WHERE job_id=$1 GROUP BY status", job_id)
    out = {"total": 0, "pending": 0, "processing": 0, "retrying": 0, "success": 0, "failed": 0, "unknown": 0, "skipped": 0}
    for r in rows:
        out[r["status"]] = r["n"]
        out["total"] += r["n"]
    return out
