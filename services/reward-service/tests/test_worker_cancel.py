"""Huỷ cưỡng bức task job không để job kẹt `running`; tranh chấp lock đa tiến trình ⇒ False (409), không 500."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from reward_service.store.repositories import lock as lock_repo
from tests.test_jobs_api import RECIPIENT, RECIPIENT2, _setup_credential, client, fake  # noqa: F401


async def test_forced_cancel_resets_job_to_paused_and_releases_lock(client, db_conn, fake):
    _, _, headers = await _setup_credential(client, db_conn, fake, channel="666666666666666601")
    job_id = (await client.post("/v1/reward/jobs", headers=headers, json={
        "name": "cancel", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
        "overrides": {"delay_ms": 2500, "jitter_ms": 0}})).json()["job_id"]
    assert (await client.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)).status_code == 202
    await asyncio.sleep(0.5)
    wm = client.app_.state.worker_manager
    task = wm._running[job_id].task
    assert await db_conn.fetchval("SELECT status FROM reward_jobs WHERE id=$1", job_id) == "running"

    task.cancel()
    await asyncio.wait([task], timeout=5)
    assert task.done() and not wm.is_running(job_id)
    assert await db_conn.fetchval("SELECT status FROM reward_jobs WHERE id=$1", job_id) == "paused"
    assert await db_conn.fetchval("SELECT count(*) FROM reward_runner_lock WHERE job_id=$1", job_id) == 0
    reasons = await db_conn.fetch("SELECT payload FROM reward_job_events WHERE job_id=$1 AND type='auto_pause'", job_id)
    assert any("WORKER_CANCELLED" in str(r["payload"]) for r in reasons)
    # job chạy lại được (không kẹt)
    assert (await client.post(f"/v1/reward/jobs/{job_id}/resume", headers=headers)).status_code == 202
    await client.post(f"/v1/reward/jobs/{job_id}/stop", headers=headers)


class _RacingConn:
    """Kết nối giả: SELECT thấy chưa có lock, nhưng INSERT thua cuộc đua (tiến trình khác vừa chiếm kênh)."""

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, *a):
        return None

    async def execute(self, sql, *a):
        if sql.lstrip().startswith("INSERT"):
            raise asyncpg.UniqueViolationError("ux_reward_lock_channel")


async def test_lock_acquire_race_loser_gets_false_not_exception():
    assert await lock_repo.acquire(
        _RacingConn(), job_id=1, channel_id="c", account_id="a", pid=1, host="h") is False
