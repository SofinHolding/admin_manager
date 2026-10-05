"""SQL của kho Idempotency-Key: (client_id, method, path, key) -> (sha256(body), response)."""

from __future__ import annotations

import json
from datetime import datetime

import asyncpg


async def reserve(conn: asyncpg.Connection, client_id: str, method: str, path: str, key: str, request_hash: str,
                  now: datetime) -> bool:
    """True nếu giữ chỗ thành công. Khoá trùng đang được transaction khác ghi: INSERT CHỜ tới khi nó commit/rollback."""
    got = await conn.fetchval(
        "INSERT INTO idempotency_keys (client_id, method, path, idem_key, request_hash, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING RETURNING idem_key", client_id, method, path, key,
        request_hash, now)
    return got is not None


async def find(conn: asyncpg.Connection, client_id: str, method: str, path: str, key: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT request_hash, status_code, response FROM idempotency_keys "
        "WHERE client_id = $1 AND method = $2 AND path = $3 AND idem_key = $4", client_id, method, path, key)
    return dict(row) if row else None


async def complete(conn: asyncpg.Connection, client_id: str, method: str, path: str, key: str, status_code: int,
                   response: dict) -> None:
    await conn.execute(
        "UPDATE idempotency_keys SET status_code = $5, response = $6 "
        "WHERE client_id = $1 AND method = $2 AND path = $3 AND idem_key = $4",
        client_id, method, path, key, status_code,
        json.dumps(response, ensure_ascii=False, separators=(",", ":")))


async def purge_before(conn: asyncpg.Connection, cutoff: datetime) -> int:
    tag = await conn.execute("DELETE FROM idempotency_keys WHERE created_at < $1", cutoff)
    return int(tag.split()[-1])
