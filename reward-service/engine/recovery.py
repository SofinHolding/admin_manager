"""Phục hồi sau crash — dịch 1-1 từ `src/reward/engine/recovery.js`.

`prepared`/`sent` ⇒ `aborted` + `error_code=COALESCE(error_code,'CRASH_DURING_SEND')`; item chưa
terminal ⇒ `unknown` (`message_posted` nếu attempt đã có `message_id`, ngược lại `none`) — TUYỆT ĐỐI
KHÔNG BAO GIỜ đặt lại `pending` (mất write-ahead sẽ cho phép gửi trùng). Job `running` ⇒ `paused`.
Giải phóng lock quá hạn > 30s. Chạy trong `lifespan`, quét TOÀN HỆ THỐNG (mọi account), TRƯỚC khi
nhận request đầu tiên. Idempotent — gọi lại nhiều lần vô hại (không còn orphan để xử lý).
"""

from __future__ import annotations

from store.pool import Pool
from store.repositories import events as events_repo
from store.repositories import lock as lock_repo

_TERMINAL = {"success", "failed", "unknown", "skipped"}


async def recover_orphaned_attempts(pool: Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            orphans = await conn.fetch("SELECT * FROM reward_attempts WHERE phase IN ('prepared','sent')")
            for a in orphans:
                await conn.execute(
                    "UPDATE reward_attempts SET phase='aborted', "
                    "error_code=COALESCE(error_code,'CRASH_DURING_SEND'), finished_at=now() WHERE id=$1",
                    a["id"],
                )
                item = await conn.fetchrow("SELECT * FROM reward_items WHERE id=$1", a["item_id"])
                if item is None or item["status"] in _TERMINAL:
                    continue
                conf_level = "message_posted" if a["message_id"] else "none"
                await conn.execute(
                    "UPDATE reward_items SET status='unknown', confirmation_level=$1, "
                    "failure_code='CRASH_DURING_SEND', finalized_at=now() WHERE id=$2",
                    conf_level, item["id"],
                )

            running_jobs = await conn.fetch("SELECT id FROM reward_jobs WHERE status='running'")
            for j in running_jobs:
                await conn.execute("UPDATE reward_jobs SET status='paused' WHERE id=$1", j["id"])
                await events_repo.add_event(conn, job_id=j["id"], type="recovery", actor="system")

        await lock_repo.release_stale(conn)
