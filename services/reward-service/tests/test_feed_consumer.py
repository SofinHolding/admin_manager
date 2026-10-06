"""Consumer feed Identity: projection/denylist, idempotent, con trỏ bền, retry/dead-letter/replay, nguồn chết,
và PHẢN ỨNG THẬT: thu quyền ⇒ job đang chạy được tạm dừng ngay + từ chối job mới (Phase 4)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reward_service.engine.worker_manager import CredentialInvalidError  # noqa: E402
from reward_service.identity_link import consumer as consumer_mod  # noqa: E402
from reward_service.store.repositories import feed as feed_repo  # noqa: E402
from reward_service.store.repositories import projection as proj  # noqa: E402
from tests.conftest import app_client, insert_account, make_consumer, sign_token  # noqa: E402
from tests.harness.fake_discord import start_fake  # noqa: E402
from tests.test_jobs_api import RECIPIENT, RECIPIENT2, _poll_job, _setup_credential  # noqa: E402

USER_ROUTE = "/v1/reward/credentials"


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _discord_env(fake) -> dict:
    return {"REWARD_API_BASE": fake.api_base, "REWARD_GATEWAY_URL": fake.gateway_url, "REWARD_HTTP_TIMEOUT_S": "2",
            "REWARD_CONFIRM_TIMEOUT_S": "1", "REWARD_CONFIRM_POLL_INTERVAL_S": "0.05",
            "REWARD_MAX_CONCURRENT_JOBS": "3", "REWARD_RECONCILE_GRACE_S": "0"}


@pytest.fixture
async def fake():
    f = await start_fake()
    try:
        yield f
    finally:
        await f.close()


@pytest.fixture
async def jclient(db_conn, identity, fake):
    async with app_client(identity, **_discord_env(fake)) as ac:
        yield ac


@pytest.fixture
async def aclient(db_conn, identity, fake):
    """Consumer chạy NỀN thật (poll 50ms) — kiểm chứng đường event → pause không cần test gọi tay."""
    async with app_client(identity, feed="auto", poll_interval="0.05", **_discord_env(fake)) as ac:
        yield ac


async def _projection(db_conn, account_id):
    return await db_conn.fetchrow("SELECT * FROM account_projection WHERE account_id = $1", account_id)


async def _denials(db_conn, account_id):
    return sorted(r["reason"] for r in await db_conn.fetch(
        "SELECT reason FROM account_denylist WHERE account_id = $1", account_id))


# ── Projection + denylist ────────────────────────────────────────────────────────

async def test_registered_role_profile_events_build_projection(client, db_conn, identity):
    user = identity.add_user(role="viewer", username="proj_a", emit_event=True)
    identity.emit("account.role_changed", {"account_id": user["id"], "role": "admin", "previous": "viewer"})
    identity.emit("account.profile_updated", {"account_id": user["id"], "username": "proj_renamed"})
    assert await make_consumer(client, identity).poll_once() == 3
    row = await _projection(db_conn, user["id"])
    assert (row["username"], row["role"], row["status"]) == ("proj_renamed", "admin", "active")


async def test_permission_revoked_and_granted_toggle_denylist_and_revoke_credentials(client, db_conn, identity):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    await db_conn.execute("INSERT INTO reward_discord_credentials (account_id, status) VALUES ($1, 'valid')", user["id"])
    token = sign_token(user)
    consumer = make_consumer(client, identity)

    identity.revoke_permission(user["id"], "reward:access")
    await consumer.poll_once()
    assert await _denials(db_conn, user["id"]) == ["permission_revoked"]
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == 403  # token còn hạn nhưng bị chặn NGAY
    assert await db_conn.fetchval(
        "SELECT status FROM reward_discord_credentials WHERE account_id = $1", user["id"]) == "revoked"
    assert (await _projection(db_conn, user["id"]))["reward_access"] is False

    identity.grant_permission(user["id"], "reward:access")
    await consumer.poll_once()
    assert await _denials(db_conn, user["id"]) == []
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code == 200
    assert (await _projection(db_conn, user["id"]))["reward_access"] is True


async def test_other_permissions_are_ignored(client, db_conn, identity):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access", "reward:admin"))
    identity.revoke_permission(user["id"], "reward:admin")
    assert await make_consumer(client, identity).poll_once() == 1
    assert await _denials(db_conn, user["id"]) == []
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(user)))).status_code == 200


async def test_suspended_blocks_and_active_lifts_independent_of_permission_denial(client, db_conn, identity):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    token = sign_token(user)
    consumer = make_consumer(client, identity)
    identity.revoke_permission(user["id"], "reward:access")
    identity.set_status(user["id"], "suspended")
    await consumer.poll_once()
    assert await _denials(db_conn, user["id"]) == ["permission_revoked", "session_revoked", "suspended"]

    identity.set_status(user["id"], "active")
    await consumer.poll_once()
    # gỡ khoá KHÔNG được âm thầm trả lại quyền đã bị thu
    assert await _denials(db_conn, user["id"]) == ["permission_revoked", "session_revoked"]
    assert (await _projection(db_conn, user["id"]))["status"] == "active"
    assert (await client.get(USER_ROUTE, headers=_hdr(token))).status_code in (401, 403)


async def test_deleted_is_permanent_and_session_revoke_only_hits_older_tokens(client, db_conn, identity):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    consumer = make_consumer(client, identity)
    old_token = sign_token(user, iat=int(time.time()) - 5)
    identity.emit("session.revoked", {"account_id": user["id"]})
    await consumer.poll_once()
    assert (await client.get(USER_ROUTE, headers=_hdr(old_token))).status_code == 401
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(user, iat=int(time.time()) + 20)))).status_code == 200

    identity.delete_account(user["id"])
    await consumer.poll_once()
    assert "deleted" in await _denials(db_conn, user["id"])
    identity.emit("account.permission_changed", {"account_id": user["id"], "permission": "reward:access", "granted": True})
    await consumer.poll_once()
    assert "deleted" in await _denials(db_conn, user["id"])  # cấp quyền không hồi sinh tài khoản đã xoá
    assert (await _projection(db_conn, user["id"]))["status"] == "deleted"


async def test_session_revoked_with_sid_only_blocks_that_session(client, db_conn, identity):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    identity.emit("session.revoked", {"account_id": user["id"], "sid": "sid-a"})
    await make_consumer(client, identity).poll_once()
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(user, sid="sid-a")))).status_code == 401
    assert (await client.get(USER_ROUTE, headers=_hdr(sign_token(user, sid="sid-b")))).status_code == 200


async def test_old_session_denials_are_pruned_but_blanket_ones_are_not(client, db_conn, identity):
    await proj.add_denial(db_conn, account_id="acc-old", reason=proj.SESSION_REASON, sid="x")
    await proj.add_denial(db_conn, account_id="acc-old", reason="suspended")
    await db_conn.execute("UPDATE account_denylist SET denied_at = now() - interval '2 days'")
    await make_consumer(client, identity).poll_once()
    assert await _denials(db_conn, "acc-old") == ["suspended"]


# ── Idempotent, con trỏ bền, batch ───────────────────────────────────────────────

async def test_redelivered_events_are_noops_and_do_not_repeat_side_effects(client, db_conn, identity):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    paused_calls: list[tuple[str, str]] = []

    async def on_pause(account_id, reason):
        paused_calls.append((account_id, reason))
        return []

    consumer = make_consumer(client, identity, on_pause=on_pause)
    event = identity.emit("account.permission_changed",
                          {"account_id": user["id"], "permission": "reward:access", "granted": False})
    assert await consumer.poll_once() == 1
    async with client.app_.state.pool.acquire() as conn:
        await feed_repo.reset_cursor(conn, "identity", 0)  # mô phỏng at-least-once: Identity giao lại từ đầu
    await consumer.poll_once()
    await consumer.poll_once()
    assert paused_calls == [(user["id"], "ROLE_REVOKED")]  # phản ứng đúng MỘT lần
    assert await db_conn.fetchval("SELECT count(*) FROM processed_events WHERE id = $1", event["id"]) == 1
    assert await db_conn.fetchval("SELECT count(*) FROM account_denylist WHERE account_id = $1", user["id"]) == 1


async def test_cursor_is_durable_and_a_new_consumer_resumes_from_it(client, db_conn, identity):
    user = identity.add_user(role="viewer", emit_event=True)
    first = make_consumer(client, identity)
    await first.poll_once()
    assert await db_conn.fetchval("SELECT seq FROM feed_cursor WHERE source = 'identity'") == identity.events[-1]["seq"]

    identity.emit("account.profile_updated", {"account_id": user["id"], "username": "sau_khi_khoi_dong_lai"})
    second = make_consumer(client, identity)  # "khởi động lại": instance mới, trạng thái chỉ còn trong DB
    identity.requests.clear()
    assert await second.poll_once() == 1
    after_values = [r["query"]["after"] for r in identity.calls("GET", "/internal/v1/feed")]
    assert after_values[0] == str(identity.events[-2]["seq"])  # đọc tiếp từ con trỏ, KHÔNG đọc lại từ 0
    assert (await _projection(db_conn, user["id"]))["username"] == "sau_khi_khoi_dong_lai"


async def test_large_backlog_is_paged_and_fully_processed(client, db_conn, identity):
    users = [identity.add_user(role="viewer", emit_event=True) for _ in range(7)]
    consumer = make_consumer(client, identity, batch_size=3)
    assert await consumer.poll_once() == 7
    assert await db_conn.fetchval("SELECT count(*) FROM account_projection") == len(users)
    assert len(identity.calls("GET", "/internal/v1/feed")) >= 3  # 7 event / lô 3


async def test_consumer_sends_service_key_and_requests_only_handled_types(client, identity):
    await make_consumer(client, identity).poll_once()
    req = identity.calls("GET", "/internal/v1/feed")[0]
    assert req["service_key"] == identity.feed_key
    assert set(req["query"]["types"].split(",")) == set(consumer_mod.HANDLED_TYPES)


# ── Retry / dead-letter / replay ─────────────────────────────────────────────────

async def test_transient_failure_is_retried_with_backoff_and_succeeds(client, db_conn, identity, monkeypatch):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    real = proj.add_denial
    attempts = {"n": 0}

    async def flaky(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("sự cố tạm thời giả lập")
        return await real(*a, **kw)

    monkeypatch.setattr("reward_service.identity_link.access.proj.add_denial", flaky)
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    consumer = make_consumer(client, identity, max_attempts=4, sleep=fake_sleep)
    identity.revoke_permission(user["id"], "reward:access")
    await consumer.poll_once()
    assert attempts["n"] == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]  # backoff mũ
    assert await _denials(db_conn, user["id"]) == ["permission_revoked"]
    assert await db_conn.fetchval("SELECT count(*) FROM dead_letters") == 0


async def test_permanent_failure_goes_to_dead_letter_and_queue_continues(client, db_conn, identity, monkeypatch):
    bad = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    good = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    real = proj.add_denial

    async def poisoned(conn, *, account_id, **kw):
        if account_id == bad["id"]:
            raise RuntimeError("lỗi cố định giả lập")
        return await real(conn, account_id=account_id, **kw)

    monkeypatch.setattr("reward_service.identity_link.access.proj.add_denial", poisoned)
    identity.revoke_permission(bad["id"], "reward:access")
    identity.revoke_permission(good["id"], "reward:access")
    consumer = make_consumer(client, identity, max_attempts=3)
    assert await consumer.poll_once() == 2

    letters = await db_conn.fetch("SELECT * FROM dead_letters")
    assert len(letters) == 1 and letters[0]["attempts"] == 3 and "RuntimeError" in letters[0]["error"]
    assert letters[0]["payload"] and letters[0]["replayed_at"] is None
    assert await _denials(db_conn, good["id"]) == ["permission_revoked"]  # event sau KHÔNG bị chặn bởi event hỏng
    assert await _denials(db_conn, bad["id"]) == []
    assert await db_conn.fetchval("SELECT seq FROM feed_cursor") == identity.events[-1]["seq"]  # con trỏ đã đi qua


async def test_replay_dead_letter_applies_it_once_fixed(client, db_conn, identity, monkeypatch):
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    real = proj.add_denial

    async def broken(*a, **kw):
        raise RuntimeError("hỏng")

    monkeypatch.setattr("reward_service.identity_link.access.proj.add_denial", broken)
    identity.revoke_permission(user["id"], "reward:access")
    consumer = make_consumer(client, identity, max_attempts=2)
    await consumer.poll_once()
    assert await db_conn.fetchval("SELECT count(*) FROM dead_letters WHERE replayed_at IS NULL") == 1
    assert await _denials(db_conn, user["id"]) == []

    monkeypatch.setattr("reward_service.identity_link.access.proj.add_denial", real)  # đã sửa lỗi
    assert await consumer.replay_dead_letters() == {"replayed": 1, "skipped": 0, "failed": 0}
    assert await _denials(db_conn, user["id"]) == ["permission_revoked"]
    assert await db_conn.fetchval("SELECT count(*) FROM dead_letters WHERE replayed_at IS NOT NULL") == 1
    assert await consumer.replay_dead_letters() == {"replayed": 0, "skipped": 0, "failed": 0}  # replay lặp = no-op


async def test_replay_skips_dead_letter_superseded_by_newer_event(client, db_conn, identity, monkeypatch):
    """Revoke vào dead-letter, rồi Grant (mới hơn) xử lý xong: replay revoke cũ KHÔNG được khoá lại người dùng."""
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    real = proj.add_denial

    async def broken(*a, **kw):
        raise RuntimeError("hỏng")

    monkeypatch.setattr("reward_service.identity_link.access.proj.add_denial", broken)
    identity.revoke_permission(user["id"], "reward:access")
    consumer = make_consumer(client, identity, max_attempts=1)
    await consumer.poll_once()
    monkeypatch.setattr("reward_service.identity_link.access.proj.add_denial", real)
    identity.grant_permission(user["id"], "reward:access")
    await consumer.poll_once()

    assert await consumer.replay_dead_letters() == {"replayed": 0, "skipped": 1, "failed": 0}
    assert await _denials(db_conn, user["id"]) == []


async def test_malformed_events_go_straight_to_dead_letter_without_retries(client, db_conn, identity):
    identity.emit("account.permission_changed", {"permission": "reward:access", "granted": False})  # thiếu account_id
    identity.emit("account.permission_changed", {"account_id": "x", "permission": "reward:access"})  # thiếu granted
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    consumer = make_consumer(client, identity, max_attempts=5, sleep=fake_sleep)
    assert await consumer.poll_once() == 2
    rows = await db_conn.fetch("SELECT attempts FROM dead_letters")
    assert [r["attempts"] for r in rows] == [1, 1] and sleeps == []


# ── Nguồn feed chết / lỗi: không crash, giữ con trỏ ──────────────────────────────

async def test_dead_source_does_not_crash_keeps_cursor_and_catches_up_later(client, db_conn, identity):
    user = identity.add_user(role="viewer", emit_event=True)
    consumer = make_consumer(client, identity)
    await consumer.poll_once()
    cursor = await db_conn.fetchval("SELECT seq FROM feed_cursor")
    token = identity.token(identity.users[user["id"]])
    assert (await client.get("/v1/reward/health")).status_code == 200

    await identity.stop()
    identity.emit("account.profile_updated", {"account_id": user["id"], "username": "khi_nguon_chet"})
    assert await consumer.poll_once() == 0
    assert consumer.last_error and consumer.consecutive_failures == 1
    assert identity.feed_key not in consumer.last_error  # không rò khoá
    assert await db_conn.fetchval("SELECT seq FROM feed_cursor") == cursor
    assert (await client.get("/v1/reward/health")).status_code == 200  # service không bị ảnh hưởng

    await identity.start(identity.port)
    assert await consumer.poll_once() == 1 and consumer.last_error is None
    assert (await _projection(db_conn, user["id"]))["username"] == "khi_nguon_chet"
    assert token


async def test_http_500_and_wrong_key_are_survived(client, db_conn, identity):
    consumer = make_consumer(client, identity)
    identity.feed_failures = 5
    assert await consumer.poll_once() == 0 and consumer.last_error
    identity.feed_failures = 0
    identity.feed_key = "khoa-da-bi-xoay-0123456789abcdef"  # Identity xoay khoá, Reward còn khoá cũ → 401
    assert await consumer.poll_once() == 0 and "từ chối khoá" in consumer.last_error


async def test_background_loop_survives_unexpected_errors(client, db_conn, identity, monkeypatch):
    consumer = make_consumer(client, identity, poll_interval=0.01)
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise RuntimeError("không lường trước")

    monkeypatch.setattr(consumer, "poll_once", boom)
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.3)
    assert not task.done() and calls["n"] >= 1  # vòng nền không chết
    consumer.stop()
    await asyncio.wait_for(task, timeout=2)


# ── Phản ứng thật: tạm dừng job đang chạy + từ chối job mới ───────────────────────

def _revoke_permission(identity, acct):
    identity.revoke_permission(acct, "reward:access")


def _suspend(identity, acct):
    identity.set_status(acct, "suspended")


def _delete(identity, acct):
    identity.delete_account(acct)


def _session_revoked(identity, acct):
    identity.emit("session.revoked", {"account_id": acct})


@pytest.mark.parametrize("trigger,blanket", [
    (_revoke_permission, True), (_suspend, True), (_delete, True), (_session_revoked, False),
], ids=["permission_revoked", "suspended", "deleted", "session_revoked"])
async def test_running_job_is_paused_immediately_and_new_jobs_are_refused(jclient, db_conn, identity, fake, trigger, blanket):
    fake.interaction_plan = None
    account, token, headers = await _setup_credential(
        jclient, db_conn, fake, channel="777777777777777771", role="viewer", perms=("reward:access",))
    admin = await insert_account(db_conn, role="admin")
    admin_headers = _hdr(sign_token(admin))
    created = await jclient.post("/v1/reward/jobs", headers=headers, json={
        "name": "se-bi-dung", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
        "overrides": {"delay_ms": 2500, "jitter_ms": 0}})
    job_id = created.json()["job_id"]
    spare = (await jclient.post("/v1/reward/jobs", headers=headers, json={
        "name": "job-khac", "raw_text": f"{RECIPIENT}|1\n"})).json()["job_id"]
    assert (await jclient.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)).status_code == 202
    await asyncio.sleep(0.3)  # item đầu xong, runner đang nghỉ delay trước item 2

    consumer = make_consumer(jclient, identity)  # dựng trước (khởi tạo httpx/SSL tốn ~0.5s trên Windows)
    trigger(identity, account["id"])
    wm = jclient.app_.state.worker_manager
    started = time.monotonic()
    await consumer.poll_once()
    assert time.monotonic() - started < 1  # từ khi có event tới khi runner nhận lệnh tạm dừng
    # "Ngay": ngay khi consumer xử lý xong event, runner ĐÃ nhận yêu cầu tạm dừng (chưa kịp gửi item 2)
    assert wm.is_running(job_id) is False or wm._running[job_id].runner._pause_requested

    final = await _poll_job(jclient, admin_headers, job_id, statuses=("paused",), timeout_s=10.0)
    assert final["status"] == "paused"
    items = (await jclient.get(f"/v1/reward/jobs/{job_id}/items", headers=admin_headers)).json()["items"]
    assert sorted(i["status"] for i in items) == ["pending", "success"]  # item 2 KHÔNG bị gửi
    assert len(fake.interactions) == 1
    events = [e["payload"] for e in (await jclient.get(f"/v1/reward/jobs/{job_id}/events", headers=admin_headers)
                                     ).json()["events"] if e["type"] == "auto_pause"]
    assert any(json.loads(p or "{}").get("source") == "identity" for p in events)

    # Từ chối khởi chạy job mới NGAY (token vẫn còn hạn)
    again = await jclient.post(f"/v1/reward/jobs/{spare}/run", headers=headers)
    assert again.status_code in (401, 403)
    assert (await jclient.post("/v1/reward/jobs", headers=headers, json={"name": "n", "raw_text": "1|1"})
            ).status_code in (401, 403)
    if blanket:  # tầng engine cũng tự chặn (không phụ thuộc tầng HTTP)
        with pytest.raises(CredentialInvalidError):
            await wm.start(spare)
    assert len(fake.interactions) == 1


async def test_granting_back_lifts_denial_and_job_can_resume(jclient, db_conn, identity, fake):
    fake.interaction_plan = None
    account, token, headers = await _setup_credential(
        jclient, db_conn, fake, channel="777777777777777772", role="viewer", perms=("reward:access",))
    job_id = (await jclient.post("/v1/reward/jobs", headers=headers, json={
        "name": "tiep-tuc", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
        "overrides": {"delay_ms": 600, "jitter_ms": 0}})).json()["job_id"]
    await jclient.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)
    await asyncio.sleep(0.25)
    consumer = make_consumer(jclient, identity)
    identity.revoke_permission(account["id"], "reward:access")
    await consumer.poll_once()
    admin_headers = _hdr(sign_token(await insert_account(db_conn, role="admin")))
    await _poll_job(jclient, admin_headers, job_id, statuses=("paused",), timeout_s=10.0)

    identity.grant_permission(account["id"], "reward:access")
    await consumer.poll_once()
    await db_conn.execute("UPDATE reward_discord_credentials SET status = 'valid' WHERE account_id = $1", account["id"])
    resumed = await jclient.post(f"/v1/reward/jobs/{job_id}/resume", headers=headers)
    assert resumed.status_code == 202, resumed.text
    done = await _poll_job(jclient, headers, job_id, statuses=("completed",), timeout_s=15.0)
    assert done["counts"]["success"] == 2


async def test_background_consumer_pauses_running_job_without_manual_poll(aclient, db_conn, identity, fake):
    fake.interaction_plan = None
    account, token, headers = await _setup_credential(
        aclient, db_conn, fake, channel="777777777777777773", role="viewer", perms=("reward:access",))
    admin_headers = _hdr(sign_token(await insert_account(db_conn, role="admin")))
    job_id = (await aclient.post("/v1/reward/jobs", headers=headers, json={
        "name": "nen", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
        "overrides": {"delay_ms": 1500, "jitter_ms": 0}})).json()["job_id"]
    await aclient.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)
    await asyncio.sleep(0.3)
    # Khoá tài khoản (không động tới credential nên chỉ có đường pause/denylist mới dừng được job); KHÔNG gọi poll_once.
    identity.set_status(account["id"], "suspended")
    final = await _poll_job(aclient, admin_headers, job_id, statuses=("paused",), timeout_s=10.0)
    assert final["status"] == "paused" and len(fake.interactions) == 1
    assert await _denials(db_conn, account["id"]) == ["session_revoked", "suspended"]


async def test_job_of_another_account_is_untouched_by_someone_elses_revocation(jclient, db_conn, identity, fake):
    fake.interaction_plan = None
    victim, _, vh = await _setup_credential(jclient, db_conn, fake, channel="777777777777777774", role="viewer",
                                           perms=("reward:access",))
    bystander, _, bh = await _setup_credential(jclient, db_conn, fake, channel="777777777777777775", role="viewer",
                                              perms=("reward:access",))
    jobs = {}
    for name, h in (("v", vh), ("b", bh)):
        jobs[name] = (await jclient.post("/v1/reward/jobs", headers=h, json={
            "name": name, "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
            "overrides": {"delay_ms": 400, "jitter_ms": 0}})).json()["job_id"]
        await jclient.post(f"/v1/reward/jobs/{jobs[name]}/run", headers=h)
    await asyncio.sleep(0.2)
    identity.revoke_permission(victim["id"], "reward:access")
    await make_consumer(jclient, identity).poll_once()
    admin_headers = _hdr(sign_token(await insert_account(db_conn, role="admin")))
    await _poll_job(jclient, admin_headers, jobs["v"], statuses=("paused",), timeout_s=10.0)
    done = await _poll_job(jclient, bh, jobs["b"], statuses=("completed",), timeout_s=15.0)
    assert done["counts"]["success"] == 2


async def test_running_job_survives_identity_death(jclient, db_conn, identity, fake):
    """Fault isolation: Identity chết giữa chừng ⇒ job đang chạy KHÔNG bị ảnh hưởng và vẫn hoàn tất."""
    fake.interaction_plan = None
    account, token, headers = await _setup_credential(
        jclient, db_conn, fake, channel="777777777777777776", role="viewer", perms=("reward:access",))
    job_id = (await jclient.post("/v1/reward/jobs", headers=headers, json={
        "name": "van-chay", "raw_text": f"{RECIPIENT}|5\n{RECIPIENT2}|5\n",
        "overrides": {"delay_ms": 400, "jitter_ms": 0}})).json()["job_id"]
    await jclient.post(f"/v1/reward/jobs/{job_id}/run", headers=headers)
    await asyncio.sleep(0.15)
    await identity.stop()
    done = await _poll_job(jclient, headers, job_id, statuses=("completed",), timeout_s=15.0)
    assert done["counts"]["success"] == 2 and len(fake.interactions) == 2


# ── Engine tự bảo vệ (lưới an toàn khi không có callback pause) ────────────────────

async def test_engine_loop_auto_pauses_when_account_is_denylisted(pool):
    from reward_service.store.repositories import events as events_repo
    from tests.conftest import seed_active_account_with_credential
    from tests.test_engine_slash import _run
    fake = await start_fake()
    try:
        async with pool.acquire() as conn:
            account_id = await seed_active_account_with_credential(conn)
            await proj.add_denial(conn, account_id=account_id, reason="permission_revoked")
        job_row, items, dist = await _run(pool, fake, rows=[{"raw_username": RECIPIENT, "point": 5}],
                                          account_id=account_id)
        assert job_row["status"] == "paused" and items[0]["status"] == "pending" and dist == 0
        assert len(fake.interactions) == 0
        async with pool.acquire() as conn:
            ev = [e for e in await events_repo.list_events(conn, job_row["id"]) if e["type"] == "auto_pause"]
        assert ev and "ROLE_REVOKED" in str(ev[0]["payload"])
    finally:
        await fake.close()
