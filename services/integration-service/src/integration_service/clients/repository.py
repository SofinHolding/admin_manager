"""SQL của integration_clients + account_projection (read model nuôi bằng feed Identity)."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def insert(conn: asyncpg.Connection, *, id: str, owner_account_id: str, tool_name: str, client_kind: str,
                 status: str, allowed_platforms: list[str], allowed_scopes: list[str], created_from: str,
                 contract_version: str, now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO integration_clients (id, owner_account_id, tool_name, client_kind, status, contract_version, "
        "allowed_platforms, allowed_scopes, created_from, created_at, updated_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10) RETURNING *",
        id, owner_account_id, tool_name, client_kind, status, contract_version, allowed_platforms, allowed_scopes,
        created_from, now)
    return dict(row)


async def get(conn: asyncpg.Connection, client_id: str, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM integration_clients WHERE id = $1" + (" FOR UPDATE" if lock else ""),
                              client_id)
    return dict(row) if row else None


async def list_for(conn: asyncpg.Connection, *, owner: str | None, status: str | None, limit: int, offset: int) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM integration_clients WHERE ($1::text IS NULL OR owner_account_id = $1) "
        "AND ($2::text IS NULL OR status = $2) ORDER BY created_at, id LIMIT $3 OFFSET $4", owner, status, limit, offset)
    return [dict(r) for r in rows]


async def count_for_owner(conn: asyncpg.Connection, owner: str) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM integration_clients WHERE owner_account_id = $1 AND status <> 'deleted'", owner)


async def by_owner(conn: asyncpg.Connection, owner: str, *, lock: bool = False) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM integration_clients WHERE owner_account_id = $1 ORDER BY id"
                            + (" FOR UPDATE" if lock else ""), owner)
    return [dict(r) for r in rows]


async def set_status(conn: asyncpg.Connection, client_id: str, status: str, suspended_by: str | None,
                     now: datetime) -> None:
    await conn.execute("UPDATE integration_clients SET status = $2, suspended_by = $3, updated_at = $4 WHERE id = $1",
                       client_id, status, suspended_by, now)


async def update_fields(conn: asyncpg.Connection, client_id: str, fields: dict, now: datetime) -> dict:
    allowed = {"tool_name", "allowed_platforms", "allowed_scopes"}
    sets, args = [], [client_id, now]
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(key)
        args.append(value)
        sets.append(f"{key} = ${len(args)}")
    sets.append("updated_at = $2")
    row = await conn.fetchrow(f"UPDATE integration_clients SET {', '.join(sets)} WHERE id = $1 RETURNING *", *args)
    return dict(row)


# ── account_projection ────────────────────────────────────────────────────────────

async def get_projection(conn: asyncpg.Connection, account_id: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM account_projection WHERE account_id = $1", account_id)
    return dict(row) if row else None


async def upsert_projection(conn: asyncpg.Connection, account_id: str, *, now: datetime, username: str | None = None,
                            status: str | None = None, email_verified: bool | None = None,
                            status_seq: int | None = None, fill_only: bool = False) -> dict:
    """Ghi `account_projection`.

    `status_seq`: seq của event đặt `status` (chỉ tăng, GREATEST) — để replay event cũ nhận ra mình đã bị vượt.
    `fill_only` (replay `account.registered`): chỉ TẠO hàng nếu chưa có / điền username còn rỗng; KHÔNG ghi đè `status`
    hay `email_verified` mà event mới hơn đã đặt."""
    if fill_only:
        row = await conn.fetchrow(
            "INSERT INTO account_projection (account_id, username, status, email_verified, updated_at, status_seq) "
            "VALUES ($1, COALESCE($2, ''), COALESCE($3, 'active'), COALESCE($4, false), $5, COALESCE($6, 0)) "
            "ON CONFLICT (account_id) DO UPDATE SET username = CASE WHEN account_projection.username = '' "
            "THEN COALESCE($2, '') ELSE account_projection.username END, updated_at = $5 RETURNING *",
            account_id, username, status, email_verified, now, status_seq)
        return dict(row)
    row = await conn.fetchrow(
        "INSERT INTO account_projection (account_id, username, status, email_verified, updated_at, status_seq) "
        "VALUES ($1, COALESCE($2, ''), COALESCE($3, 'active'), COALESCE($4, false), $5, COALESCE($6, 0)) "
        "ON CONFLICT (account_id) DO UPDATE SET username = COALESCE($2, account_projection.username), "
        "status = COALESCE($3, account_projection.status), "
        "email_verified = COALESCE($4, account_projection.email_verified), updated_at = $5, "
        "status_seq = CASE WHEN $3::text IS NOT NULL THEN GREATEST(account_projection.status_seq, COALESCE($6, 0)) "
        "ELSE account_projection.status_seq END RETURNING *",
        account_id, username, status, email_verified, now, status_seq)
    return dict(row)
