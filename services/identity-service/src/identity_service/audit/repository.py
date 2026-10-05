"""SQL cho bảng `auth_audit` — nhật ký đăng nhập và thay đổi quyền/trạng thái của chính Identity."""

from __future__ import annotations

import json
from datetime import datetime

import asyncpg


async def record(conn: asyncpg.Connection, *, account_id: str, event_type: str, now: datetime,
                 target_type: str = "", target_id: str = "", payload: dict | None = None) -> None:
    await conn.execute(
        "INSERT INTO auth_audit (account_id, event_type, target_type, target_id, payload, at) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6)",
        account_id, event_type, target_type, target_id,
        json.dumps(payload or {}, ensure_ascii=False), now)
