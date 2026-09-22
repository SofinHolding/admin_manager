"""Test end-to-end `/v1/reward/jobs/*` + `engine/worker_manager.py` (plan C.3/C.5/D.3) qua ASGI
transport + fake Discord offline + Postgres thật.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from tests.conftest import insert_account, sign_token
from tests.harness.fake_discord import start_fake

RECIPIENT = "685096940144295977"
RECIPIENT2 = "685096940144295978"
GUILD = "111111111111111111"


@pytest.fixture
async def fake():
    f = await start_fake()
    try:
        yield f
    finally:
        await f.close()


@pytest.fixture
async def client(db_conn, fake):
    os.environ["REWARD_API_BASE"] = fake.api_base
    os.environ["REWARD_GATEWAY_URL"] = fake.gateway_url
    os.environ["REWARD_HTTP_TIMEOUT_S"] = "2"
    os.environ["REWARD_CONFIRM_TIMEOUT_S"] = "1"
    os.environ["REWARD_CONFIRM_POLL_INTERVAL_S"] = "0.05"
    os.environ["REWARD_MAX_CONCURRENT_JOBS"] = "3"
    import importlib
    import main as main_module
    importlib.reload(main_module)
    app = main_module.app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac


async def _setup_credential(client, db_conn, fake, *, channel: str, role: str = "discord") -> tuple[dict, str, dict]:
    account = await insert_account(db_conn, role=role)
    token = sign_token(account)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.put(
        "/v1/reward/credentials", headers=headers,
        json={
            "token": f"tok-{account['id']}", "guild_id": GUILD, "channel_id": channel,
            "command_name": fake.command_name, "confirm_mode": "reply", "success_pattern": "has been given",
        })
    assert resp.status_code == 200, resp.text
    return account, token, headers


async def _poll_job(client, headers, job_id, *, statuses, timeout_s=10.0):
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/v1/reward/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in statuses:
            return body
        await asyncio.sleep(0.1)
    raise AssertionError(f"Job {job_id} không đạt trạng thái {statuses} sau {timeout_s}s (cuối: {body['status']})")


# ── C.3: tạo job — parse + validate ────────────────────────────────────────

async def test_create_job_parses_validates_issues_and_items(client, db_conn, fake):
    _, _, headers = await _setup_credential(client, db_conn, fake, channel="333333333333333333")
    raw_text = f"{RECIPIENT}|5\nbaduser|-3\n{RECIPIENT}|7\n"
    resp = await client.post("/v1/reward/jobs", headers=headers, json={"name": "job1", "raw_text": raw_text})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "invalid"  # dòng "baduser|-3" sai định dạng → cả job invalid
    assert body["total_items"] == 2  # nhưng 2 dòng hợp lệ vẫn được chèn để xem trước
    codes = {i["code"] for i in body["issues"]}
    assert "INVALID_ROW" in codes
    assert "DUPLICATE_USERNAME" in codes
    assert body["counts"]["pending"] == 2

    job_id = body["job_id"]
    items_resp = await client.get(f"/v1/reward/jobs/{job_id}/items", headers=headers)
    items = items_resp.json()["items"]
    assert len(items) == 2
    assert all(i["status"] == "pending" for i in items)


async def test_create_job_all_invalid_rows_is_invalid_status(client, db_conn, fake):
    _, _, headers = await _setup_credential(client, db_conn, fake, channel="333333333333333334")
    resp = await client.post(
        "/v1/reward/jobs", headers=headers, json={"name": "bad-job", "raw_text": "onlybad|0\n"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["total_items"] == 0


# ── C.3/D.3: chạy job qua worker → confirm success ──────────────────────────

async def test_run_job_end_to_end_success_via_worker(client, db_conn, fake):
    _, _, headers = await _setup_credential(client, db_conn, fake, channel="444444444444444444")
    resp = await client.post(
        "/v1/reward/jobs", headers=headers,
        json={
            "name": "run-job", "raw_text": f"{RECIPIENT}|5\n",
            "overrides": {"delay_ms": 0, "jitter_ms": 0},
        })
    job_id = resp.json()["job_id"]

    run_resp = await client.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)
    assert run_resp.status_code == 202, run_resp.text
    assert run_resp.json()["status"] == "running"

    final = await _poll_job(client, headers, job_id, statuses=("completed", "paused", "stopped"))
    assert final["status"] == "completed"
    assert final["counts"]["success"] == 1

    items = (await client.get(f"/v1/reward/jobs/{job_id}/items", headers=headers)).json()["items"]
    assert items[0]["status"] == "success"
    assert items[0]["resolved_user_id"] == RECIPIENT

    async with db_conn.transaction():
        dist_count = await db_conn.fetchval(
            "SELECT COUNT(*) FROM reward_distributions WHERE job_id=$1", job_id)
    assert dist_count == 1


# ── D.3: lock theo channel — cùng kênh 409, khác kênh song song ─────────────

async def test_lock_same_channel_second_run_gets_409(client, db_conn, fake):
    channel = "555555555555555551"
    fake.interaction_plan = ["hang"]
    _, _, headers_a = await _setup_credential(client, db_conn, fake, channel=channel)
    account_b, _, headers_b = await _setup_credential(client, db_conn, fake, channel=channel, role="discord")

    job_a = (await client.post(
        "/v1/reward/jobs", headers=headers_a,
        json={"name": "a", "raw_text": f"{RECIPIENT}|5\n", "overrides": {"delay_ms": 0, "jitter_ms": 0}},
    )).json()["job_id"]
    job_b = (await client.post(
        "/v1/reward/jobs", headers=headers_b,
        json={"name": "b", "raw_text": f"{RECIPIENT}|5\n", "overrides": {"delay_ms": 0, "jitter_ms": 0}},
    )).json()["job_id"]

    run_a = await client.post(f"/v1/reward/jobs/{job_a}/run", headers=headers_a)
    assert run_a.status_code == 202
    run_b = await client.post(f"/v1/reward/jobs/{job_b}/run", headers=headers_b)
    assert run_b.status_code == 409, run_b.text


async def test_lock_different_channel_runs_in_parallel(client, db_conn, fake):
    _, _, headers_a = await _setup_credential(client, db_conn, fake, channel="666666666666666661")
    _, _, headers_b = await _setup_credential(client, db_conn, fake, channel="666666666666666662")

    job_a = (await client.post(
        "/v1/reward/jobs", headers=headers_a,
        json={
            "name": "a", "raw_text": f"{RECIPIENT}|5\n",
            "overrides": {"delay_ms": 0, "jitter_ms": 0, "confirm_mode": "off"},
        },
    )).json()["job_id"]
    job_b = (await client.post(
        "/v1/reward/jobs", headers=headers_b,
        json={
            "name": "b", "raw_text": f"{RECIPIENT2}|5\n",
            "overrides": {"delay_ms": 0, "jitter_ms": 0, "confirm_mode": "off"},
        },
    )).json()["job_id"]

    run_a = await client.post(f"/v1/reward/jobs/{job_a}/run", headers=headers_a)
    run_b = await client.post(f"/v1/reward/jobs/{job_b}/run", headers=headers_b)
    assert run_a.status_code == 202
    assert run_b.status_code == 202

    final_a = await _poll_job(client, headers_a, job_a, statuses=("completed",))
    final_b = await _poll_job(client, headers_b, job_b, statuses=("completed",))
    assert final_a["status"] == "completed"
    assert final_b["status"] == "completed"


# ── D.3: 2 account dùng token riêng của chính mình ──────────────────────────

async def test_two_accounts_use_their_own_token(client, db_conn, fake):
    fake.valid_tokens = None
    account_a, _, headers_a = await _setup_credential(client, db_conn, fake, channel="777777777777777771")
    account_b, _, headers_b = await _setup_credential(client, db_conn, fake, channel="777777777777777772")

    job_a = (await client.post(
        "/v1/reward/jobs", headers=headers_a,
        json={
            "name": "a", "raw_text": f"{RECIPIENT}|5\n",
            "overrides": {"delay_ms": 0, "jitter_ms": 0, "confirm_mode": "off"},
        },
    )).json()["job_id"]
    await client.post(f"/v1/reward/jobs/{job_a}/run", headers=headers_a)
    await _poll_job(client, headers_a, job_a, statuses=("completed",))

    job_b = (await client.post(
        "/v1/reward/jobs", headers=headers_b,
        json={
            "name": "b", "raw_text": f"{RECIPIENT2}|5\n",
            "overrides": {"delay_ms": 0, "jitter_ms": 0, "confirm_mode": "off"},
        },
    )).json()["job_id"]
    await client.post(f"/v1/reward/jobs/{job_b}/run", headers=headers_b)
    await _poll_job(client, headers_b, job_b, statuses=("completed",))

    assert len(fake.interaction_tokens) == 2
    assert fake.interaction_tokens[0] == f"tok-{account_a['id']}"
    assert fake.interaction_tokens[1] == f"tok-{account_b['id']}"
    assert fake.interaction_tokens[0] != fake.interaction_tokens[1]


# ── C.5: thu role khi job đang chạy → pause, item còn lại pending ──────────

async def test_revoke_discord_while_running_pauses_job(client, db_conn, fake):
    fake.interaction_plan = None
    account, _, headers = await _setup_credential(client, db_conn, fake, channel="888888888888888881")
    admin = await insert_account(db_conn, role="admin")
    admin_token = sign_token(admin)

    job_id = (await client.post(
        "/v1/reward/jobs", headers=headers,
        json={
            "name": "revoke-me", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
            "overrides": {"delay_ms": 800, "jitter_ms": 0},
        },
    )).json()["job_id"]

    run_resp = await client.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)
    assert run_resp.status_code == 202

    await asyncio.sleep(0.3)  # để item đầu tiên kịp xử lý xong, đang trong khoảng delay
    revoke_resp = await client.post(
        f"/v1/reward/admin/accounts/{account['id']}/revoke-discord",
        headers={"Authorization": f"Bearer {admin_token}"}, json={})
    assert revoke_resp.status_code == 200, revoke_resp.text

    final = await _poll_job(client, headers, job_id, statuses=("paused",), timeout_s=10.0)
    assert final["status"] == "paused"

    items = (await client.get(f"/v1/reward/jobs/{job_id}/items", headers=headers)).json()["items"]
    statuses = [i["status"] for i in items]
    assert "pending" in statuses  # item còn lại KHÔNG bị xử lý tiếp sau khi role bị thu


# ── Token không lộ log/response ─────────────────────────────────────────────

async def test_token_never_leaks_into_logs_or_responses(client, db_conn, fake, caplog):
    fake.valid_tokens = None
    caplog.set_level(logging.DEBUG)
    secret_token = "SUPER-SECRET-DISCORD-TOKEN-XYZ"
    account = await insert_account(db_conn, role="discord")
    token = sign_token(account)
    headers = {"Authorization": f"Bearer {token}"}
    put_resp = await client.put(
        "/v1/reward/credentials", headers=headers,
        json={
            "token": secret_token, "guild_id": GUILD, "channel_id": "999999999999999991",
            "command_name": fake.command_name, "success_pattern": "has been given",
        })
    assert put_resp.status_code == 200
    assert secret_token not in put_resp.text

    job_id = (await client.post(
        "/v1/reward/jobs", headers=headers,
        json={
            "name": "leak-check", "raw_text": f"{RECIPIENT}|5\n",
            "overrides": {"delay_ms": 0, "jitter_ms": 0},
        },
    )).json()["job_id"]
    run_resp = await client.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)
    assert secret_token not in run_resp.text
    final = await _poll_job(client, headers, job_id, statuses=("completed", "paused", "stopped"))
    assert secret_token not in str(final)

    for record in caplog.records:
        assert secret_token not in record.getMessage()


# ── pause / resume / stop ───────────────────────────────────────────────────

async def test_pause_resume_stop_lifecycle(client, db_conn, fake):
    fake.interaction_plan = None
    _, _, headers = await _setup_credential(client, db_conn, fake, channel="101010101010101011")
    job_id = (await client.post(
        "/v1/reward/jobs", headers=headers,
        json={
            "name": "pause-resume", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
            "overrides": {"delay_ms": 800, "jitter_ms": 0},
        },
    )).json()["job_id"]

    run_resp = await client.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)
    assert run_resp.status_code == 202
    await asyncio.sleep(0.3)

    pause_resp = await client.post(f"/v1/reward/jobs/{job_id}/pause", headers=headers)
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    items_after_pause = (await client.get(f"/v1/reward/jobs/{job_id}/items", headers=headers)).json()["items"]
    assert any(i["status"] == "pending" for i in items_after_pause)

    resume_resp = await client.post(f"/v1/reward/jobs/{job_id}/resume", headers=headers)
    assert resume_resp.status_code == 202
    final = await _poll_job(client, headers, job_id, statuses=("completed", "stopped", "paused"), timeout_s=10.0)
    assert final["status"] == "completed"

    # Chạy job mới rồi stop hẳn
    job_id2 = (await client.post(
        "/v1/reward/jobs", headers=headers,
        json={
            "name": "to-stop", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
            "overrides": {"delay_ms": 800, "jitter_ms": 0},
        },
    )).json()["job_id"]
    await client.post(f"/v1/reward/jobs/{job_id2}/run", headers=headers)
    await asyncio.sleep(0.3)
    stop_resp = await client.post(f"/v1/reward/jobs/{job_id2}/stop", headers=headers)
    assert stop_resp.status_code == 200
    assert stop_resp.json()["status"] == "stopped"
