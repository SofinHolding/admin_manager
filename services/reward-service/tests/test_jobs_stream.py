"""SSE `/v1/reward/jobs/{id}/stream`: kiểm lại quyền định kỳ (thu quyền giữa chừng ⇒ đóng stream) và tự đóng khi job
không chạy mà không đổi gì (không giữ kết nối vô hạn)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from reward_service.api import jobs as jobs_mod
from tests.test_jobs_api import RECIPIENT, _setup_credential, client, fake  # noqa: F401


async def _validated_job(client, db_conn, fake, channel):
    account, _, headers = await _setup_credential(client, db_conn, fake, channel=channel)
    resp = await client.post("/v1/reward/jobs", headers=headers, json={"name": "sse", "raw_text": f"{RECIPIENT}|5\n"})
    assert resp.status_code == 201 and resp.json()["status"] == "validated", resp.text
    return account, headers, resp.json()["job_id"]


async def test_stream_closes_when_access_revoked_midway(client, db_conn, fake, monkeypatch):
    monkeypatch.setattr(jobs_mod, "SSE_POLL_IDLE_S", 0.05)
    monkeypatch.setattr(jobs_mod, "SSE_AUTH_RECHECK_S", 0.1)
    account, headers, job_id = await _validated_job(client, db_conn, fake, "555555555555555501")
    task = asyncio.create_task(client.get(f"/v1/reward/jobs/{job_id}/stream", headers=headers))
    await asyncio.sleep(0.4)
    assert not task.done()  # job validated: stream vẫn mở
    await db_conn.execute(
        "INSERT INTO account_denylist (account_id, reason, sid) VALUES ($1, 'permission_revoked', '')", account["id"])
    resp = await asyncio.wait_for(task, timeout=5)
    assert "event: revoked" in resp.text and "permission_revoked" in resp.text


async def test_stream_closes_after_idle_when_job_not_running(client, db_conn, fake, monkeypatch):
    monkeypatch.setattr(jobs_mod, "SSE_POLL_IDLE_S", 0.05)
    monkeypatch.setattr(jobs_mod, "SSE_IDLE_CLOSE_S", 0.5)
    _, headers, job_id = await _validated_job(client, db_conn, fake, "555555555555555502")
    resp = await asyncio.wait_for(client.get(f"/v1/reward/jobs/{job_id}/stream", headers=headers), timeout=5)
    assert ": idle" in resp.text and "event: job" in resp.text
