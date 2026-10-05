"""Bảng `reward_role_audit` — audit RIÊNG của Reward cho thao tác quản trị quyền reward.

Quyền do Identity quản lý; Reward chỉ ghi lại ai đã yêu cầu gì và Identity trả lời ra sao (`outcome`,
`identity_status`). `from_role`/`to_role` mô tả trạng thái quyền `reward:access` (hoặc nhãn legacy) trước/sau.
"""

from __future__ import annotations

from typing import Any

import asyncpg


async def record(
    conn: asyncpg.Connection, *, account_id: str, actor_id: str, from_role: str, to_role: str, reason: str,
    outcome: str, identity_status: int | None,
) -> None:
    await conn.execute(
        "INSERT INTO reward_role_audit (account_id, actor_account_id, from_role, to_role, reason, outcome, "
        "identity_status) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        account_id, actor_id, from_role, to_role, reason, outcome, identity_status)


async def list_entries(conn: asyncpg.Connection, *, account_id: str | None, limit: int) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    cols = "id, account_id, actor_account_id, from_role, to_role, reason, outcome, identity_status, at"
    if account_id:
        rows = await conn.fetch(
            f"SELECT {cols} FROM reward_role_audit WHERE account_id = $1 ORDER BY at DESC, id DESC LIMIT $2",
            account_id, limit)
    else:
        rows = await conn.fetch(f"SELECT {cols} FROM reward_role_audit ORDER BY at DESC, id DESC LIMIT $1", limit)
    return [dict(r) for r in rows]
