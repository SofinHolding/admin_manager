"""SQL cho `integration_provisioning`: yêu cầu tạo Integration client ghi cùng transaction với account."""

from __future__ import annotations

import json
from datetime import datetime

import asyncpg


async def create(conn: asyncpg.Connection, *, account_id: str, now: datetime, client_id: str, tool_name: str,
                 client_kind: str, platforms: list[str], scopes: list[str],
                 contract_version: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO integration_provisioning (client_id, account_id, tool_name, client_kind, platforms, scopes, "
        "created_at, updated_at) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $7)",
        client_id, account_id, tool_name, client_kind, json.dumps(platforms), json.dumps(scopes), now)


async def get_for_account(conn: asyncpg.Connection, account_id: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT client_id, status, error_code FROM integration_provisioning WHERE account_id = $1 "
        "ORDER BY created_at DESC LIMIT 1", account_id)
    return dict(row) if row else None
