"""SQL của jobs. Mọi hàm nhận `conn` (trong transaction của người gọi) và `now` do service truyền vào (đồng hồ thống nhất)."""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncpg

TERMINAL = ("done", "failed", "dead", "cancelled")


async def insert(conn: asyncpg.Connection, *, job_id: str, owner: str, resource_id: str | None, platform: str, kind: str,
                 required_capability: str, priority: int, payload: dict, max_attempts: int, dedupe_key: str | None,
                 created_by: str, now: datetime, resource_owner_id: str | None = None) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO jobs (id, owner_account_id, resource_id, platform, kind, required_capability, status, priority, "
        "payload, max_attempts, run_after, dedupe_key, created_by, created_at, updated_at, resource_owner_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,'pending',$7,$8,$9,$10,$11,$12,$10,$10,$13) RETURNING *",
        job_id, owner, resource_id, platform, kind, required_capability, priority, payload, max_attempts, now,
        dedupe_key, created_by, resource_owner_id)
    return dict(row)


async def get(conn: asyncpg.Connection, job_id: str, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1" + (" FOR UPDATE" if lock else ""), job_id)
    return dict(row) if row else None


async def find_active_by_dedupe(conn: asyncpg.Connection, owner: str, dedupe_key: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM jobs WHERE owner_account_id = $1 AND dedupe_key = $2 AND status IN ('pending', 'leased')",
        owner, dedupe_key)
    return dict(row) if row else None


async def count_pending(conn: asyncpg.Connection, owner: str) -> int:
    return await conn.fetchval("SELECT count(*) FROM jobs WHERE owner_account_id = $1 AND status IN ('pending', 'leased')",
                               owner)


async def reclaim_expired(conn: asyncpg.Connection, now: datetime, *, limit: int = 500) -> list[dict]:
    """Lease hết hạn => job quay lại hàng đợi (`pending`); đã dùng hết lượt thử => `dead`. Lượt thử được tính lúc claim.

    `SKIP LOCKED`: worker khác đang xử lý cùng job (complete/fail đang giữ khoá) thì bỏ qua, không chờ."""
    rows = await conn.fetch(
        "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END, "
        "lease_until = NULL, lease_token = NULL, run_after = $1, last_error_code = 'LEASE_EXPIRED', "
        "last_error_message = 'Lease hết hạn mà chưa complete/fail', updated_at = $1, "
        "completed_at = CASE WHEN attempts >= max_attempts THEN $1 ELSE NULL END "
        "WHERE id IN (SELECT id FROM jobs WHERE status = 'leased' AND lease_until <= $1 ORDER BY lease_until "
        "LIMIT $2 FOR UPDATE SKIP LOCKED) RETURNING id, status, owner_account_id", now, limit)
    return [dict(r) for r in rows]


async def claim(conn: asyncpg.Connection, *, owner: str, system: bool, platforms: list[str], capabilities: list[str],
                limit: int, lease_seconds: int, client_id: str, installation_id: str, now: datetime) -> list[dict]:
    """Claim NGUYÊN TỬ: chọn + khoá + chuyển sang `leased` trong MỘT câu lệnh.

    * `FOR UPDATE SKIP LOCKED`: hai worker tranh nhau không bao giờ nhận cùng một job và không chặn nhau.
    * Ownership: không có scope hệ thống thì CHỈ job của chính owner.
    * Capability: job đòi capability nào thì installation phải có capability đó."""
    rows = await conn.fetch(
        "WITH picked AS ("
        "  SELECT id FROM jobs WHERE status = 'pending' AND run_after <= $1 AND platform = ANY($2::text[]) "
        "    AND ($3::boolean OR owner_account_id = $4) "
        "    AND (required_capability = '' OR required_capability = ANY($5::text[])) "
        "  ORDER BY priority DESC, run_after, created_at, id LIMIT $6 FOR UPDATE SKIP LOCKED) "
        "UPDATE jobs j SET status = 'leased', attempts = j.attempts + 1, lease_until = $7, claimed_by_client = $8, "
        "claimed_by_installation = $9, lease_token = replace(gen_random_uuid()::text, '-', ''), updated_at = $1 "
        "FROM picked WHERE j.id = picked.id RETURNING j.*",
        now, platforms, system, owner, capabilities, limit, now + timedelta(seconds=lease_seconds), client_id,
        installation_id)
    return sorted((dict(r) for r in rows), key=lambda r: (-r["priority"], r["run_after"], r["created_at"], r["id"]))


async def resource_summaries(conn: asyncpg.Connection, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = await conn.fetch("SELECT id, platform, resource_type, external_id, data FROM resources WHERE id = ANY($1::text[])",
                            ids)
    return {r["id"]: dict(r) for r in rows}


async def update_state(conn: asyncpg.Connection, row: dict, **fields) -> dict:
    """Ghi các cột trạng thái của job (SQL tĩnh): `row` là bản ghi hiện tại (đang giữ khoá), `fields` là thay đổi."""
    new = {**row, **fields}
    out = await conn.fetchrow(
        "UPDATE jobs SET status = $2, result = $3, lease_until = $4, lease_token = $5, last_error_code = $6, "
        "last_error_message = $7, run_after = $8, completed_at = $9, updated_at = $10 WHERE id = $1 RETURNING *",
        row["id"], new["status"], new["result"], new["lease_until"], new["lease_token"], new["last_error_code"],
        new["last_error_message"], new["run_after"], new["completed_at"], new["updated_at"])
    return dict(out)


async def list_for(conn: asyncpg.Connection, *, owner: str | None, status: str | None, limit: int, offset: int) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM jobs WHERE ($1::text IS NULL OR owner_account_id = $1) AND ($2::text IS NULL OR status = $2) "
        "ORDER BY created_at DESC, id LIMIT $3 OFFSET $4", owner, status, limit, offset)
    return [dict(r) for r in rows]


async def cancel_pending_for_resource(conn: asyncpg.Connection, resource_id: str, now: datetime) -> int:
    """Resource bị gỡ / không còn chạy bằng Fallback Tool => huỷ job CHƯA giao (job đang lease để worker tự kết thúc)."""
    tag = await conn.execute(
        "UPDATE jobs SET status = 'cancelled', completed_at = $2, updated_at = $2 "
        "WHERE resource_id = $1 AND status = 'pending'", resource_id, now)
    return int(tag.rsplit(" ", 1)[-1])
