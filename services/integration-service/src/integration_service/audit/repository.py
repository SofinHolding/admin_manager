"""Audit: ai (account/client/installation/key) đã làm gì, lúc nào, kết quả ra sao. Không ghi secret/payload thô."""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg

logger = logging.getLogger("integration.audit")


async def insert(conn: asyncpg.Connection, *, at: datetime, action: str, result: str, account_id: str | None = None,
                 actor_account_id: str | None = None, client_id: str | None = None, installation_id: str | None = None,
                 key_id: str | None = None, snapshot_id: str | None = None, resource_id: str | None = None,
                 request_id: str = "-", ip: str = "", status_code: int | None = None,
                 detail: dict | None = None) -> None:
    await conn.execute(
        "INSERT INTO audit_log (account_id, actor_account_id, client_id, installation_id, key_id, action, "
        "snapshot_id, resource_id, request_id, ip, result, status_code, detail, at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
        account_id, actor_account_id, client_id, installation_id, key_id, action, snapshot_id, resource_id,
        request_id, ip, result, status_code, detail or {}, at)


async def query(conn: asyncpg.Connection, *, client_id: str | None, account_id: str | None, action: str | None,
                result: str | None, before_id: int | None, limit: int) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM audit_log WHERE ($1::text IS NULL OR client_id = $1) AND ($2::text IS NULL OR account_id = $2) "
        "AND ($3::text IS NULL OR action = $3) AND ($4::text IS NULL OR result = $4) "
        "AND ($5::bigint IS NULL OR id < $5) ORDER BY id DESC LIMIT $6",
        client_id, account_id, action, result, before_id, limit)
    return [dict(r) for r in rows]
