"""Hiệu lực CỤC BỘ của thay đổi quyền `reward:access` — dùng chung cho consumer feed và thao tác quản trị.

Một nguồn duy nhất cho "thu quyền ⇒ denylist + thu hồi cấu hình Discord" và "cấp quyền ⇒ gỡ denylist", idempotent.
Tạm dừng job nằm ở `WorkerManager.pause_account_jobs` (cần tiến trình), do caller gọi sau khi transaction commit.
"""

from __future__ import annotations

import asyncpg

from store.repositories import projection as proj


async def apply_reward_access(
    conn: asyncpg.Connection, *, account_id: str, granted: bool, event_id: str | None = None,
) -> None:
    async with conn.transaction():
        await proj.upsert_projection(conn, account_id=account_id, reward_access=granted)
        if granted:
            await proj.lift_denial(conn, account_id=account_id, reason="permission_revoked")
        else:
            await proj.add_denial(conn, account_id=account_id, reason="permission_revoked", event_id=event_id)
            # Giữ hành vi cũ của revoke_discord: cấu hình Discord của tài khoản bị thu quyền → 'revoked'.
            await conn.execute(
                "UPDATE reward_discord_credentials SET status = 'revoked', updated_at = now() WHERE account_id = $1",
                account_id)
