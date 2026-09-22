"""Truy vấn bảng `accounts` của admin_manager — CHỈ `SELECT` và `UPDATE accounts SET role`.

BẤT BIẾN (xem plan A.2, A.4, "Không động vào" của Task Brief P1): không được thêm bất kỳ câu
INSERT/DELETE/ALTER nào chạm `accounts`, và tuyệt đối không chạm `invite_keys`, `refresh_tokens`,
`documents`, `user_meta`. `role` là `text DEFAULT 'viewer'` không CHECK/ENUM (db.py `_PG_DDL`) nên gán
`role='discord'` bằng SQL trực tiếp không bị chặn bởi pydantic `^(viewer|admin)$` của `admin_api.py`.
"""

from __future__ import annotations

from typing import Any

from store.pool import Pool


class AccountNotFound(Exception):
    """Không tìm thấy `accounts.id` tương ứng."""


class RoleChangeConflict(Exception):
    """Đổi role bị từ chối do bất biến nghiệp vụ (xem A.4.1) — role hiện tại đính kèm để báo lỗi rõ."""

    def __init__(self, current_role: str) -> None:
        self.current_role = current_role
        super().__init__(current_role)


async def get_by_id(pool: Pool, account_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, email, role, status, created_at, last_login FROM accounts WHERE id = $1",
            account_id)
    return dict(row) if row else None


async def list_accounts(
    pool: Pool, *, q: str | None = None, role: str | None = None, status: str | None = None,
) -> list[dict[str, Any]]:
    """JOIN đọc `accounts`, làm giàu bằng số liệu `reward_*` (Phase 1: các bảng reward_* còn rỗng)."""
    clauses: list[str] = []
    params: list[Any] = []
    if q:
        params.append(f"%{q.lower()}%")
        clauses.append(f"(lower(a.username) LIKE ${len(params)} OR lower(a.email) LIKE ${len(params)})")
    if role:
        params.append(role)
        clauses.append(f"a.role = ${len(params)}")
    if status:
        params.append(status)
        clauses.append(f"a.status = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT a.id, a.username, a.email, a.role, a.status, a.created_at, a.last_login, "
        "       (c.account_id IS NOT NULL) AS has_credential, c.status AS credential_status, "
        "       COALESCE(j.jobs_total, 0) AS jobs_total, COALESCE(j.jobs_running, 0) AS jobs_running "
        "FROM accounts a "
        "LEFT JOIN reward_discord_credentials c ON c.account_id = a.id "
        "LEFT JOIN (SELECT account_id, count(*) AS jobs_total, "
        "                  count(*) FILTER (WHERE status = 'running') AS jobs_running "
        "           FROM reward_jobs GROUP BY account_id) j ON j.account_id = a.id "
        f"{where} ORDER BY a.created_at DESC"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(row) for row in rows]


async def grant_discord(pool: Pool, *, target_id: str, actor_id: str, reason: str) -> dict[str, Any]:
    """`UPDATE accounts SET role='discord' WHERE id=$1 AND role<>'admin' AND status='active'` + audit.

    Một transaction duy nhất cùng `INSERT reward_role_audit` (xem A.4.1)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, role, status FROM accounts WHERE id = $1 FOR UPDATE", target_id)
            if row is None:
                raise AccountNotFound(target_id)
            if row["role"] == "admin" or row["status"] != "active":
                raise RoleChangeConflict(row["role"])
            from_role = row["role"]
            updated = await conn.fetchrow(
                "UPDATE accounts SET role = 'discord' WHERE id = $1 RETURNING id, username, role", target_id)
            await conn.execute(
                "INSERT INTO reward_role_audit (account_id, actor_account_id, from_role, to_role, reason) "
                "VALUES ($1, $2, $3, $4, $5)",
                target_id, actor_id, from_role, "discord", reason)
    return {"id": updated["id"], "username": updated["username"], "from_role": from_role, "to_role": "discord"}


async def revoke_discord(pool: Pool, *, target_id: str, actor_id: str, reason: str) -> dict[str, Any]:
    """`UPDATE accounts SET role='viewer' WHERE id=$1 AND role='discord'` + audit + pause job đang chạy.

    C.5 quy tắc chéo #1: thu role ⇒ dừng việc — Phase 1 `reward_jobs` còn rỗng nên câu UPDATE luôn trả
    0 dòng, nhưng phải hiện diện đúng ngữ nghĩa cho các phase sau."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, role, status FROM accounts WHERE id = $1 FOR UPDATE", target_id)
            if row is None:
                raise AccountNotFound(target_id)
            if row["role"] != "discord":
                raise RoleChangeConflict(row["role"])
            updated = await conn.fetchrow(
                "UPDATE accounts SET role = 'viewer' WHERE id = $1 RETURNING id, username, role", target_id)
            await conn.execute(
                "INSERT INTO reward_role_audit (account_id, actor_account_id, from_role, to_role, reason) "
                "VALUES ($1, $2, $3, $4, $5)",
                target_id, actor_id, "discord", "viewer", reason)
            await conn.execute(
                "UPDATE reward_discord_credentials SET status = 'revoked', updated_at = now() WHERE account_id = $1",
                target_id)
            paused = await conn.fetch(
                "UPDATE reward_jobs SET status = 'paused' WHERE account_id = $1 AND status IN "
                "('running', 'stopping') RETURNING id",
                target_id)
    return {
        "id": updated["id"], "username": updated["username"], "to_role": "viewer",
        "paused_jobs": [row["id"] for row in paused],
    }


async def list_role_audit(pool: Pool, *, account_id: str | None, limit: int) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    if account_id:
        sql = (
            "SELECT id, account_id, actor_account_id, from_role, to_role, reason, at "
            "FROM reward_role_audit WHERE account_id = $1 ORDER BY at DESC LIMIT $2"
        )
        params: tuple[Any, ...] = (account_id, limit)
    else:
        sql = (
            "SELECT id, account_id, actor_account_id, from_role, to_role, reason, at "
            "FROM reward_role_audit ORDER BY at DESC LIMIT $1"
        )
        params = (limit,)
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(row) for row in rows]
