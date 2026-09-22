"""Bảng `reward_runner_lock` — 1 kênh Discord chỉ 1 job chạy tại một thời điểm (plan B.3 mục 8)."""

from __future__ import annotations

import asyncpg

LOCK_STALE_MS = 30000


async def acquire(
    conn: asyncpg.Connection, *, job_id: int, channel_id: str, account_id: str, pid: int, host: str,
) -> bool:
    """Chiếm lock theo `channel_id`. Được phép khi: chưa có lock / lock cùng job / lock cũ hơn 30s."""
    async with conn.transaction():
        existing = await conn.fetchrow("SELECT * FROM reward_runner_lock WHERE channel_id=$1", channel_id)
        if existing is not None:
            age_ms = await conn.fetchval(
                "SELECT EXTRACT(EPOCH FROM (now() - $1)) * 1000", existing["heartbeat_at"])
            if age_ms < LOCK_STALE_MS and existing["job_id"] != job_id:
                return False
            await conn.execute("DELETE FROM reward_runner_lock WHERE channel_id=$1", channel_id)
        await conn.execute(
            "INSERT INTO reward_runner_lock (job_id, account_id, channel_id, pid, host, heartbeat_at) "
            "VALUES ($1,$2,$3,$4,$5, now())",
            job_id, account_id, channel_id, pid, host,
        )
        return True


async def renew(conn: asyncpg.Connection, *, job_id: int) -> None:
    await conn.execute("UPDATE reward_runner_lock SET heartbeat_at=now() WHERE job_id=$1", job_id)


async def release(conn: asyncpg.Connection, *, job_id: int) -> None:
    await conn.execute("DELETE FROM reward_runner_lock WHERE job_id=$1", job_id)


async def release_stale(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "DELETE FROM reward_runner_lock WHERE EXTRACT(EPOCH FROM (now() - heartbeat_at)) * 1000 >= $1",
        LOCK_STALE_MS,
    )
