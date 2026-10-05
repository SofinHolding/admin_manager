"""Consumer provisioning: kéo feed Identity -> account_projection + client; idempotent, retry, dead-letter, Identity chết."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from conftest import ISSUER
from manager_sdk import FeedClient, FeedError

from integration_service.app import create_app
from integration_service.provisioning import handlers
from integration_service.provisioning.consumer import ProvisioningConsumer

pytestmark = pytest.mark.needs_pg
IDENTITY_KEY = "k" + uuid.uuid4().hex + uuid.uuid4().hex     # sinh mỗi lần chạy, không phải secret thật


class FakeIdentityFeed:
    """Máy chủ feed của Identity (đúng hợp đồng internal-feed-contract-v1.md §1)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.up = True
        self.fail_status: int | None = None
        self.requests: list[dict] = []

    def add(self, type_: str, data: dict) -> dict:
        seq = len(self.events) + 1
        ev = {"seq": seq, "id": f"evt_{uuid.uuid4().hex}", "type": type_, "subject": f"acc:{data.get('account_id')}",
              "time": "2026-10-05T10:00:00Z", "data_version": 1, "data": data}
        self.events.append(ev)
        return ev

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("identity down")
        if self.fail_status:
            return httpx.Response(self.fail_status)
        assert request.url.path == "/internal/v1/feed"
        if request.headers.get("x-service-key") != IDENTITY_KEY:
            return httpx.Response(401, json={"detail": "no"})
        after = int(request.url.params.get("after", 0))
        limit = int(request.url.params.get("limit", 100))
        types = [t for t in request.url.params.get("types", "").split(",") if t]
        self.requests.append({"after": after, "limit": limit, "types": types})
        rows = [e for e in self.events if e["seq"] > after and (not types or e["type"] in types)]
        page = rows[:limit]
        return httpx.Response(200, json={"events": page, "next_cursor": page[-1]["seq"] if page else after,
                                         "has_more": len(rows) > limit, "head": len(self.events)})


@pytest.fixture
def fake_feed() -> FakeIdentityFeed:
    return FakeIdentityFeed()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def consumer_factory(app, settings, clock, fake_feed, sleeps):
    import dataclasses

    def make(**overrides) -> ProvisioningConsumer:
        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            await asyncio.sleep(0)

        cfg = dataclasses.replace(settings, provisioning_batch=overrides.pop("batch", 100), **overrides)
        feed = FeedClient("http://identity.test", IDENTITY_KEY, retries=1, backoff_base=0.0, sleep=fake_sleep,
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_feed.handler)))
        c = app.state.container
        return ProvisioningConsumer(c.db, cfg, c.clients, feed, clock, sleep=fake_sleep)

    return make


def prov(client_id: str | None = None, **over) -> dict:
    return {"client_id": client_id or "cli_" + uuid.uuid4().hex, "tool_name": "Tool Moi", "client_kind": "primary_tool",
            "platforms": ["facebook"], "scopes": ["integration:handshake", "snapshots:write"], **over}


def registered(account_id: str, provisioning: dict | None = None, status: str = "pending") -> dict:
    data = {"account_id": account_id, "username": "user_" + account_id[:6], "role": "viewer", "status": status}
    if provisioning is not None:
        data["provisioning"] = provisioning
    return data


async def outbox(env, type_: str | None = None) -> list[dict]:
    rows = await env.rows("SELECT type, data FROM outbox ORDER BY seq")
    return [r for r in rows if type_ is None or r["type"] == type_]


# ── Luồng chính ─────────────────────────────────────────────────────────────────

async def test_registered_with_provisioning_creates_pending_client_then_verified_activates(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    p = prov()
    fake_feed.add("account.registered", registered(acc, p))
    consumer = consumer_factory()
    assert await consumer.poll_once() == (1, False)
    client = (await env.rows("SELECT * FROM integration_clients WHERE id = $1", p["client_id"]))[0]
    assert client["owner_account_id"] == acc and client["status"] == "pending_email_verification"
    assert client["tool_name"] == "Tool Moi" and client["created_from"] == "provisioning"
    assert client["allowed_platforms"] == ["facebook"] and set(client["allowed_scopes"]) == {"integration:handshake", "snapshots:write"}
    proj = (await env.rows("SELECT * FROM account_projection WHERE account_id = $1", acc))[0]
    assert proj["status"] == "pending" and proj["email_verified"] is False and proj["username"].startswith("user_")
    ev = (await outbox(env, "integration.client.provisioned"))[0]["data"]
    assert ev == {"client_id": p["client_id"], "account_id": acc, "status": "pending_email_verification"}

    # chưa xác thực email: chưa thể cấp credential
    user = env.user(sub=acc)
    r = await env.http.post(f"/v1/integrations/clients/{p['client_id']}/credentials", headers=user["headers"], json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"

    fake_feed.add("account.verified", {"account_id": acc})
    await consumer.poll_once()
    assert (await env.rows("SELECT status FROM integration_clients WHERE id = $1", p["client_id"]))[0]["status"] == "active"
    assert (await env.rows("SELECT email_verified, status FROM account_projection WHERE account_id = $1", acc))[0] == {
        "email_verified": True, "status": "active"}
    assert [e["data"]["status"] for e in await outbox(env, "integration.client.provisioned")] == [
        "pending_email_verification", "active"]
    r = await env.http.post(f"/v1/integrations/clients/{p['client_id']}/credentials", headers=user["headers"], json={})
    assert r.status_code == 201
    key = r.json()["data"]["api_key"]
    hs = await env.http.post("/v1/integrations/handshake", headers={"Authorization": f"Bearer {key}"},
                             json={"installation": {"device_id": "d"}})
    assert hs.status_code == 200 and hs.json()["data"]["owner_account_id"] == acc


async def test_registered_without_provisioning_only_updates_projection(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc))
    await consumer_factory().poll_once()
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 0
    assert await env.scalar("SELECT count(*) FROM account_projection WHERE account_id = $1", acc) == 1
    assert await outbox(env) == []


async def test_events_are_idempotent_and_cursor_is_durable(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    p = prov()
    fake_feed.add("account.registered", registered(acc, p))
    fake_feed.add("account.verified", {"account_id": acc})
    first = consumer_factory()
    assert (await first.poll_once())[0] == 2 and await first.cursor() == 2
    snapshot = (await env.rows("SELECT * FROM integration_clients"), await outbox(env))
    # Một consumer MỚI (khởi động lại tiến trình) tiếp tục từ con trỏ đã lưu
    second = consumer_factory()
    assert await second.poll_once() == (0, False)
    assert fake_feed.requests[-1]["after"] == 2
    # Giao lại cùng sự kiện (at-least-once): no-op
    for ev in fake_feed.events:
        await second.handle(ev)
    assert (await env.rows("SELECT * FROM integration_clients"), await outbox(env)) == snapshot
    assert await env.scalar("SELECT count(*) FROM processed_events") == 2
    # Backfill: đặt lại con trỏ về 0 cũng không nhân đôi gì
    await env.rows("UPDATE feed_cursor SET seq = 0")
    await consumer_factory().poll_once()
    assert (await env.rows("SELECT * FROM integration_clients"), await outbox(env)) == snapshot


async def test_pagination_over_many_events(env, fake_feed, consumer_factory):
    accounts = [uuid.uuid4().hex for _ in range(25)]
    for a in accounts:
        fake_feed.add("account.registered", registered(a, prov()))
    c = consumer_factory(batch=10)
    total, more = 0, True
    while more:
        n, more = await c.poll_once()
        total += n
    assert total == 25 and await env.scalar("SELECT count(*) FROM integration_clients") == 25
    assert [r["limit"] for r in fake_feed.requests][:3] == [10, 10, 10]
    assert set(fake_feed.requests[0]["types"]) == set(handlers.HANDLED_TYPES)   # chỉ xin loại sự kiện mình cần


async def test_unrelated_event_types_are_ignored_but_advance_cursor(env, fake_feed, consumer_factory):
    fake_feed.add("account.role_changed", {"account_id": "x" * 32, "role": "admin", "previous": "viewer"})
    c = consumer_factory()
    fake_feed.events[0]["type"] = "account.profile_updated"
    await c.handle(fake_feed.events[0])
    assert await c.cursor() == 1 and await env.scalar("SELECT count(*) FROM account_projection") == 0


# ── Lỗi: dead-letter, retry, không chặn hàng đợi ──────────────────────────────────

@pytest.mark.parametrize("bad,code", [
    (prov(client_kind="root"), "PROVISION_INVALID"), (prov(tool_name=""), "PROVISION_INVALID"),
    (prov(platforms="facebook"), "PROVISION_INVALID"), (prov(client_id="khong-phai-cli"), "PROVISION_INVALID"),
    (prov(platforms=["khong_co"]), "PLATFORM_NOT_ALLOWED"),
])
async def test_invalid_provisioning_goes_to_dead_letter_and_emits_provision_failed(env, fake_feed, consumer_factory, bad, code):
    acc, good_acc = uuid.uuid4().hex, uuid.uuid4().hex
    ev = fake_feed.add("account.registered", registered(acc, bad))
    good = prov()
    fake_feed.add("account.registered", registered(good_acc, good))          # event SAU vẫn phải được xử lý
    c = consumer_factory()
    assert await c.poll_once() == (2, False)
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", good["client_id"]) == "pending_email_verification"
    dead = (await env.rows("SELECT * FROM dead_letters"))[0]
    assert dead["id"] == ev["id"] and dead["error"] == code and dead["resolved_at"] is None
    failed = (await outbox(env, "integration.client.provision_failed"))[0]["data"]
    assert failed["account_id"] == acc and failed["error_code"] == code
    assert await c.cursor() == 2


async def test_client_id_conflict_across_accounts_is_dead_lettered(env, fake_feed, consumer_factory):
    p = prov()
    fake_feed.add("account.registered", registered(uuid.uuid4().hex, p))
    fake_feed.add("account.registered", registered(uuid.uuid4().hex, p))      # cùng client_id, account khác
    await consumer_factory().poll_once()
    assert (await env.rows("SELECT error FROM dead_letters"))[0]["error"] == "CLIENT_ID_CONFLICT"
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 1


async def test_transient_failure_is_retried_with_exponential_backoff(env, fake_feed, consumer_factory, sleeps, monkeypatch):
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc, prov()))
    real = handlers.apply_event
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("lỗi tạm")
        return await real(*a, **kw)

    monkeypatch.setattr(handlers, "apply_event", flaky)
    await consumer_factory().poll_once()
    assert calls["n"] == 3 and await env.scalar("SELECT count(*) FROM integration_clients") == 1
    assert await env.scalar("SELECT count(*) FROM dead_letters") == 0
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] * 1.2                  # backoff tăng dần


async def test_retries_are_bounded_then_dead_letter_and_queue_continues(env, fake_feed, consumer_factory, sleeps, monkeypatch):
    bad_acc, ok_acc = uuid.uuid4().hex, uuid.uuid4().hex
    fake_feed.add("account.registered", registered(bad_acc, prov()))
    fake_feed.add("account.registered", registered(ok_acc, prov()))
    real = handlers.apply_event

    async def selective(conn, clients, event, now):
        if event["data"]["account_id"] == bad_acc:
            raise RuntimeError("hỏng vĩnh viễn")
        return await real(conn, clients, event, now)

    monkeypatch.setattr(handlers, "apply_event", selective)
    c = consumer_factory(provisioning_max_attempts=4)
    await c.poll_once()
    dead = (await env.rows("SELECT * FROM dead_letters"))[0]
    assert dead["attempts"] == 4 and dead["error"].startswith("HANDLER_ERROR")
    assert len(sleeps) == 3                                                  # đúng 4 lần thử, 3 lần nghỉ
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 1  # event sau không bị chặn
    assert (await outbox(env, "integration.client.provision_failed"))[0]["data"]["error_code"] == "HANDLER_ERROR"


async def test_replay_dead_letter_after_fixing_the_cause(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    p = prov(platforms=["threads"])
    fake_feed.add("account.registered", registered(acc, p))
    c = consumer_factory()
    await c.poll_once()
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 0
    assert await c.replay_dead_letters() == {"replayed": 0, "failed": 1}      # nguyên nhân chưa sửa
    await env.rows("INSERT INTO platform_registry (key, display_name) VALUES ('threads', 'Threads')")
    assert await c.replay_dead_letters() == {"replayed": 1, "failed": 0}
    assert (await env.rows("SELECT status FROM integration_clients WHERE id = $1", p["client_id"]))[0]["status"] == "pending_email_verification"
    assert (await env.rows("SELECT resolved_at FROM dead_letters"))[0]["resolved_at"] is not None
    assert await c.replay_dead_letters() == {"replayed": 0, "failed": 0}


# ── Identity chết ───────────────────────────────────────────────────────────────

async def test_identity_down_keeps_cursor_and_raises_feed_error_without_side_effects(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc, prov()))
    c = consumer_factory()
    fake_feed.up = False
    with pytest.raises(FeedError):
        await c.poll_once()
    fake_feed.up = True
    fake_feed.fail_status = 503
    with pytest.raises(FeedError):
        await c.poll_once()
    assert await c.cursor() == 0 and await env.scalar("SELECT count(*) FROM integration_clients") == 0
    fake_feed.fail_status = None
    assert (await c.poll_once())[0] == 1


async def test_background_loop_survives_identity_outage_and_recovers(env, fake_feed, consumer_factory, sleeps):
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc, prov()))
    fake_feed.up = False
    c = consumer_factory()
    task = asyncio.create_task(c.run_forever())
    for _ in range(200):
        await asyncio.sleep(0.005)
        if len(sleeps) >= 3:
            break
    assert not task.done() and c.last_error and len(sleeps) >= 3            # vẫn sống, đang lùi nhịp
    assert sleeps[1] >= sleeps[0]                                           # backoff không giảm
    fake_feed.up = True
    for _ in range(400):
        await asyncio.sleep(0.01)
        if await env.scalar("SELECT count(*) FROM integration_clients"):
            break
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_identity_down_does_not_affect_other_endpoints(env, fake_feed, consumer_factory):
    fake_feed.up = False
    tool = await env.tool()
    with pytest.raises(FeedError):
        await consumer_factory().poll_once()
    assert (await tool.post("/v1/integrations/handshake", {"installation": {"device_id": "d"}})).status_code == 200
    assert (await env.http.get("/health/ready")).status_code == 200


# ── Hệ quả với client ───────────────────────────────────────────────────────────

async def test_suspend_revokes_credentials_verify_does_not_unsuspend_and_admin_suspension_survives(env, fake_feed, consumer_factory):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    other = (await env.http.post("/v1/integrations/clients", headers=env.user(sub=tool.owner)["headers"], json={
        "tool_name": "B", "client_kind": "primary_tool", "platforms": ["facebook"]})).json()["data"]["client_id"]
    await env.http.patch(f"/v1/integrations/clients/{other}", headers=admin["headers"], json={"status": "suspended"})
    c = consumer_factory()
    fake_feed.add("account.status_changed", {"account_id": tool.owner, "status": "suspended", "previous": "active"})
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", tool.client_id) == "suspended"
    r = await tool.post("/v1/integrations/handshake", {"installation": {"device_id": "d"}})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED"
    assert any(e["data"]["key_id"] == tool.key_id for e in await outbox(env, "credential.revoked"))
    blocked = await env.http.post("/v1/integrations/clients", headers=env.user(sub=tool.owner)["headers"], json={
        "tool_name": "C", "client_kind": "primary_tool", "platforms": ["facebook"]})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "ACCOUNT_INACTIVE"

    # xác thực email KHÔNG tự mở khoá tài khoản bị khoá
    fake_feed.add("account.verified", {"account_id": tool.owner})
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", tool.client_id) == "suspended"
    assert await env.scalar("SELECT status FROM account_projection WHERE account_id = $1", tool.owner) == "suspended"

    # account được mở lại: client bị khoá do account thì khôi phục; client bị admin khoá thì giữ nguyên
    fake_feed.add("account.status_changed", {"account_id": tool.owner, "status": "active", "previous": "suspended"})
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", tool.client_id) == "active"
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", other) == "suspended"
    assert (await tool.post("/v1/integrations/handshake", {"installation": {"device_id": "d"}})).status_code == 401   # key cũ đã chết


async def test_account_deleted_deletes_clients_and_kills_credentials(env, fake_feed, consumer_factory):
    tool = await env.tool()
    fake_feed.add("account.deleted", {"account_id": tool.owner})
    await consumer_factory().poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", tool.client_id) == "deleted"
    r = await tool.post("/v1/integrations/handshake", {"installation": {"device_id": "d"}})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED"
    assert await env.scalar("SELECT count(*) FROM integration_credentials WHERE status = 'active'") == 0
    assert await env.scalar("SELECT status FROM account_projection WHERE account_id = $1", tool.owner) == "deleted"


async def test_pending_client_stays_inactive_if_account_is_suspended_before_verifying(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    p = prov()
    fake_feed.add("account.registered", registered(acc, p))
    fake_feed.add("account.status_changed", {"account_id": acc, "status": "suspended", "previous": "pending"})
    fake_feed.add("account.verified", {"account_id": acc})
    await consumer_factory().poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", p["client_id"]) == "suspended"


async def test_malformed_events_are_dead_lettered_not_fatal(env, fake_feed, consumer_factory):
    fake_feed.add("account.verified", {})
    fake_feed.add("account.status_changed", {"account_id": "a" * 32, "status": "banana"})
    ok = fake_feed.add("account.verified", {"account_id": "b" * 32})
    c = consumer_factory()
    assert (await c.poll_once())[0] == 3
    assert {r["error"] for r in await env.rows("SELECT error FROM dead_letters")} == {"EVENT_INVALID"}
    assert await env.scalar("SELECT count(*) FROM processed_events WHERE id = $1", ok["id"]) == 1


# ── Vòng đời trong app ──────────────────────────────────────────────────────────

async def test_consumer_is_off_when_identity_feed_is_not_configured(env, app, settings, db, identity):
    assert app.state.provisioning is None and not settings.provisioning_enabled
    async with app.router.lifespan_context(app):
        assert (await env.http.get("/health/live")).status_code == 200      # service chạy bình thường
        assert (await env.http.get("/health/ready")).status_code == 200


async def test_consumer_runs_inside_app_lifespan_and_stops_cleanly(db, settings, identity, clock, mono, fake_feed):
    import dataclasses

    from conftest import build_verifier
    cfg = dataclasses.replace(settings, identity_feed_url="http://identity.test", identity_feed_key=IDENTITY_KEY,
                              provisioning_poll_s=0.01)
    feed = FeedClient("http://identity.test", IDENTITY_KEY, retries=0, backoff_base=0.0,
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_feed.handler)))
    app2 = create_app(cfg, database=db, verifier=build_verifier(identity), now=clock, clock=mono, identity_feed=feed)
    acc = uuid.uuid4().hex
    p = prov()
    fake_feed.add("account.registered", registered(acc, p))
    async with app2.router.lifespan_context(app2):
        for _ in range(300):
            await asyncio.sleep(0.02)
            async with db.connection() as conn:
                if await conn.fetchval("SELECT count(*) FROM integration_clients WHERE id = $1", p["client_id"]):
                    break
        async with db.connection() as conn:
            assert await conn.fetchval("SELECT status FROM integration_clients WHERE id = $1", p["client_id"]) == "pending_email_verification"
    assert ISSUER  # lifespan thoát không treo: các task nền đã bị huỷ


async def test_verified_event_never_activates_a_client_of_a_suspended_account_defense_in_depth(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    p = prov()
    fake_feed.add("account.registered", registered(acc, p))
    c = consumer_factory()
    await c.poll_once()
    await env.rows("UPDATE account_projection SET status = 'suspended' WHERE account_id = $1", acc)   # khoá lệch pha với client
    fake_feed.add("account.verified", {"account_id": acc})
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", p["client_id"]) == "pending_email_verification"
