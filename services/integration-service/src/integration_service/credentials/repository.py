"""SQL của integration_credentials. DB chỉ có `key_id` + sha256(secret); không bao giờ có plaintext."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def insert(conn: asyncpg.Connection, *, key_id: str, client_id: str, secret_hash: str, scopes: list[str],
                 label: str, expires_at: datetime | None, now: datetime, rotated_from: str | None = None) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO integration_credentials (key_id, client_id, secret_hash, scopes, label, expires_at, created_at, "
        "rotated_from) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
        key_id, client_id, secret_hash, scopes, label, expires_at, now, rotated_from)
    return dict(row)


async def find_for_auth(conn: asyncpg.Connection, key_id: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT cr.key_id, cr.client_id, cr.secret_hash, cr.scopes, cr.status, cr.expires_at, cr.last_used_at, "
        "c.status AS client_status, c.owner_account_id, c.allowed_platforms, c.allowed_scopes, c.client_kind, "
        "c.tool_name FROM integration_credentials cr JOIN integration_clients c ON c.id = cr.client_id "
        "WHERE cr.key_id = $1", key_id)
    return dict(row) if row else None


async def touch_last_used(conn: asyncpg.Connection, key_id: str, now: datetime, min_interval_s: int = 60) -> None:
    """Cập nhật last_used_at có điều tiết (tối đa 1 lần/phút/khoá) để mỗi request không thành một lần ghi DB."""
    await conn.execute(
        "UPDATE integration_credentials SET last_used_at = $2::timestamptz WHERE key_id = $1 "
        "AND (last_used_at IS NULL OR last_used_at < $2::timestamptz - make_interval(secs => $3::float8))", key_id, now, float(min_interval_s))


async def get(conn: asyncpg.Connection, client_id: str, key_id: str, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM integration_credentials WHERE client_id = $1 AND key_id = $2"
                              + (" FOR UPDATE" if lock else ""), client_id, key_id)
    return dict(row) if row else None


async def list_for_client(conn: asyncpg.Connection, client_id: str) -> list[dict]:
    rows = await conn.fetch("SELECT key_id, client_id, scopes, status, label, expires_at, last_used_at, created_at, "
                            "revoked_at, revoke_reason, rotated_from FROM integration_credentials "
                            "WHERE client_id = $1 ORDER BY created_at, key_id", client_id)
    return [dict(r) for r in rows]


async def count_active(conn: asyncpg.Connection, client_id: str, now: datetime) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM integration_credentials WHERE client_id = $1 AND status = 'active' "
        "AND (expires_at IS NULL OR expires_at > $2)", client_id, now)


async def revoke(conn: asyncpg.Connection, client_id: str, key_id: str, reason: str, now: datetime) -> bool:
    """True nếu vừa chuyển active -> revoked (False: đã revoked trước đó)."""
    r = await conn.fetchval(
        "UPDATE integration_credentials SET status = 'revoked', revoked_at = $3, revoke_reason = $4 "
        "WHERE client_id = $1 AND key_id = $2 AND status = 'active' RETURNING key_id", client_id, key_id, now, reason)
    return r is not None


async def revoke_all(conn: asyncpg.Connection, client_id: str, reason: str, now: datetime) -> list[str]:
    rows = await conn.fetch(
        "UPDATE integration_credentials SET status = 'revoked', revoked_at = $2, revoke_reason = $3 "
        "WHERE client_id = $1 AND status = 'active' RETURNING key_id", client_id, now, reason)
    return [r["key_id"] for r in rows]


async def set_expiry(conn: asyncpg.Connection, key_id: str, expires_at: datetime) -> None:
    await conn.execute("UPDATE integration_credentials SET expires_at = $2 WHERE key_id = $1", key_id, expires_at)
