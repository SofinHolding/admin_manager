"""Consumer provisioning: kéo feed Identity -> account_projection (+ client PERSONAL qua capability:tool_integration).

Từ thiết kế tool-key 2026-10-14 §1.3/§2.2: `account.registered` CHỈ nuôi account_projection, KHÔNG còn tạo client
(đã bỏ "đăng ký kèm integration"). Client PERSONAL chỉ được tạo tự động bởi `account.permission_changed`
(permission=`capability:tool_integration`, granted=true) — xem cuối file. Sự kiện
`integration.client.provision_failed` đã bị bỏ (không ai tiêu thụ nữa).
"""

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
        return ProvisioningConsumer(c.db, cfg, c.clients, c.credentials, feed, clock, sleep=fake_sleep)

    return make


def registered(account_id: str, status: str = "pending") -> dict:
    return {"account_id": account_id, "username": "user_" + account_id[:6], "role": "viewer", "status": status}


def permission_changed(account_id: str, *, granted: bool = True, permission: str = "capability:tool_integration") -> dict:
    return {"account_id": account_id, "permission": permission, "granted": granted}


async def outbox(env, type_: str | None = None) -> list[dict]:
    rows = await env.rows("SELECT type, data FROM outbox ORDER BY seq")
    return [r for r in rows if type_ is None or r["type"] == type_]


# ── account.registered: chỉ nuôi projection, không còn tạo client ───────────────

async def test_registered_only_updates_projection(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc))
    await consumer_factory().poll_once()
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 0
    proj = (await env.rows("SELECT * FROM account_projection WHERE account_id = $1", acc))[0]
    assert proj["status"] == "pending" and proj["email_verified"] is False and proj["username"].startswith("user_")
    assert await outbox(env) == []


# ── Cơ chế consumer chung: idempotent, phân trang, dead-letter, backoff ─────────

async def test_events_are_idempotent_and_cursor_is_durable(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc))
    fake_feed.add("account.permission_changed", permission_changed(acc))
    first = consumer_factory()
    assert (await first.poll_once())[0] == 2 and await first.cursor() == 2
    snapshot = (await env.rows("SELECT * FROM integration_clients"), await outbox(env))
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 1
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
        fake_feed.add("account.permission_changed", permission_changed(a))
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


async def test_transient_failure_is_retried_with_exponential_backoff(env, fake_feed, consumer_factory, sleeps, monkeypatch):
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc))
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
    fake_feed.add("account.permission_changed", permission_changed(bad_acc))
    fake_feed.add("account.permission_changed", permission_changed(ok_acc))
    real = handlers.apply_event

    async def selective(conn, clients, credentials, event, now, **kw):
        if event["data"]["account_id"] == bad_acc:
            raise RuntimeError("hỏng vĩnh viễn")
        return await real(conn, clients, credentials, event, now, **kw)

    monkeypatch.setattr(handlers, "apply_event", selective)
    c = consumer_factory(provisioning_max_attempts=4)
    await c.poll_once()
    dead = (await env.rows("SELECT * FROM dead_letters"))[0]
    assert dead["attempts"] == 4 and dead["error"].startswith("HANDLER_ERROR")
    assert len(sleeps) == 3                                                  # đúng 4 lần thử, 3 lần nghỉ
    assert await env.scalar("SELECT count(*) FROM integration_clients") == 1  # event sau không bị chặn (client của ok_acc)
    assert (await env.rows("SELECT owner_account_id FROM integration_clients"))[0]["owner_account_id"] == ok_acc


async def test_replay_dead_letter_after_fixing_the_cause(env, fake_feed, consumer_factory, monkeypatch):
    """`account.registered` lỗi tạm thời (giả lập) => dead-letter; sửa nguyên nhân rồi `replay_dead_letters` áp lại
    đúng một lần (idempotent qua `processed_events`), không lặp lại khi gọi lần nữa."""
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc))
    real = handlers._registered
    broken = {"on": True}

    async def flaky(conn, data, account_id, now, **kw):
        if broken["on"]:
            raise RuntimeError("lỗi tạm")
        return await real(conn, data, account_id, now, **kw)

    monkeypatch.setattr(handlers, "_registered", flaky)
    c = consumer_factory(provisioning_max_attempts=1)
    await c.poll_once()
    assert await env.scalar("SELECT count(*) FROM account_projection WHERE account_id = $1", acc) == 0
    assert await c.replay_dead_letters() == {"replayed": 0, "failed": 1}      # nguyên nhân chưa sửa
    broken["on"] = False
    assert await c.replay_dead_letters() == {"replayed": 1, "failed": 0}
    proj = (await env.rows("SELECT * FROM account_projection WHERE account_id = $1", acc))[0]
    assert proj["status"] == "pending" and proj["username"].startswith("user_")
    assert (await env.rows("SELECT resolved_at FROM dead_letters"))[0]["resolved_at"] is not None
    assert await c.replay_dead_letters() == {"replayed": 0, "failed": 0}


async def test_replaying_an_old_registered_does_not_revert_newer_projection_state(env, fake_feed, consumer_factory, monkeypatch):
    """`registered` (dead-letter do lỗi tạm thời) được xử lý SAU `verified` + `suspended` (seq mới hơn). Replay
    `registered` (cũ, `replay=True` => fill_only) KHÔNG được đảo ngược trạng thái mới hơn, chỉ điền chỗ trống."""
    acc = uuid.uuid4().hex
    real = handlers._registered
    broken = {"on": True}

    async def flaky(conn, data, account_id, now, **kw):
        if broken["on"]:
            raise RuntimeError("lỗi tạm")
        return await real(conn, data, account_id, now, **kw)

    monkeypatch.setattr(handlers, "_registered", flaky)
    fake_feed.add("account.registered", registered(acc))                    # seq 1 -> dead-letter (lỗi tạm thời)
    fake_feed.add("account.verified", {"account_id": acc})                   # seq 2
    fake_feed.add("account.status_changed", {"account_id": acc, "status": "suspended"})   # seq 3
    c = consumer_factory(provisioning_max_attempts=1)
    await c.poll_once()
    proj = (await env.rows("SELECT status, email_verified, username FROM account_projection WHERE account_id = $1", acc))[0]
    assert proj["status"] == "suspended" and proj["email_verified"] is True and proj["username"] == ""
    broken["on"] = False
    assert await c.replay_dead_letters() == {"replayed": 1, "failed": 0}
    proj = (await env.rows("SELECT status, email_verified, username, status_seq FROM account_projection WHERE account_id = $1",
                           acc))[0]
    assert proj["status"] == "suspended" and proj["email_verified"] is True      # KHÔNG bị đưa về 'pending'
    assert proj["username"].startswith("user_")                                  # chỗ trống thì được điền
    assert proj["status_seq"] == 3


async def test_replaying_an_old_status_change_does_not_override_a_newer_one(env, fake_feed, consumer_factory, monkeypatch):
    acc = uuid.uuid4().hex
    fake_feed.add("account.registered", registered(acc, status="active"))      # seq 1
    old = fake_feed.add("account.status_changed", {"account_id": acc, "status": "suspended"})   # seq 2 (lỗi tạm thời)
    fake_feed.add("account.status_changed", {"account_id": acc, "status": "active"})            # seq 3 (mới hơn)
    real = handlers._status_changed

    async def flaky(conn, clients, data, account_id, now, **kw):
        if data.get("status") == "suspended":
            raise RuntimeError("lỗi tạm thời")
        return await real(conn, clients, data, account_id, now, **kw)

    monkeypatch.setattr(handlers, "_status_changed", flaky)
    c = consumer_factory(provisioning_max_attempts=1)
    await c.poll_once()
    assert [d["id"] for d in await env.rows("SELECT id FROM dead_letters")] == [old["id"]]
    assert (await env.rows("SELECT status FROM account_projection WHERE account_id = $1", acc))[0]["status"] == "active"
    monkeypatch.setattr(handlers, "_status_changed", real)
    assert await c.replay_dead_letters() == {"replayed": 1, "failed": 0}
    assert (await env.rows("SELECT status FROM account_projection WHERE account_id = $1", acc))[0]["status"] == "active"
    # event suspended MỚI HƠN thì vẫn áp bình thường
    fake_feed.add("account.status_changed", {"account_id": acc, "status": "suspended"})        # seq 4
    await c.poll_once()
    assert (await env.rows("SELECT status FROM account_projection WHERE account_id = $1", acc))[0]["status"] == "suspended"


# ── Identity chết ───────────────────────────────────────────────────────────────

async def test_identity_down_keeps_cursor_and_raises_feed_error_without_side_effects(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc))
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
    fake_feed.add("account.permission_changed", permission_changed(acc))
    fake_feed.up = False
    c = consumer_factory()
    task = asyncio.create_task(c.run_forever())
    try:
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
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_identity_down_does_not_affect_other_endpoints(env, fake_feed, consumer_factory):
    fake_feed.up = False
    tool = await env.tool()
    with pytest.raises(FeedError):
        await consumer_factory().poll_once()
    assert (await tool.post("/v1/integrations/handshake", {"installation": {"device_id": tool.device_id}})).status_code == 200
    assert (await env.http.get("/health/ready")).status_code == 200


# ── Hệ quả với client (account.status_changed / account.deleted) ────────────────

async def test_suspend_revokes_credentials_verify_does_not_unsuspend_and_admin_suspension_survives(env, fake_feed, consumer_factory):
    """Owner (role `admin`) có client personal (auto-provision) + một client shared do chính họ tạo qua wizard.
    `account.status_changed(suspended)` khoá CẢ HAI client của owner; client đã bị admin khoá từ trước (shared)
    vẫn giữ nguyên `suspended_by` khi account suspend lần nữa (không bị ghi đè thành 'account' sai nghĩa khi đã
    suspended); xác thực email KHÔNG tự mở khoá account bị khoá; mở lại account thì client khoá-do-account được
    khôi phục còn client khoá-do-admin vẫn suspended."""
    admin = await env.verified_user(role="admin")
    tool = await env.tool(user=admin)
    other = (await env.http.post("/v1/integrations/clients", headers=admin["headers"], json={
        "tool_name": "B", "client_kind": "fallback_tool", "purpose": "test", "platforms": ["facebook"]})
            ).json()["data"]["client_id"]
    await env.http.patch(f"/v1/integrations/clients/{other}", headers=admin["headers"], json={"status": "suspended"})
    c = consumer_factory()
    fake_feed.add("account.status_changed", {"account_id": tool.owner, "status": "suspended", "previous": "active"})
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", tool.client_id) == "suspended"
    r = await tool.post("/v1/integrations/handshake", {"installation": {"device_id": tool.device_id}})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED"
    assert any(e["data"]["key_id"] == tool.key_id for e in await outbox(env, "credential.revoked"))
    assert await env.scalar("SELECT suspended_by FROM integration_clients WHERE id = $1", other) == "admin"

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
    assert (await tool.post("/v1/integrations/handshake", {"installation": {"device_id": tool.device_id}})).status_code == 401   # key cũ đã chết


async def test_account_deleted_deletes_clients_and_kills_credentials(env, fake_feed, consumer_factory):
    tool = await env.tool()
    fake_feed.add("account.deleted", {"account_id": tool.owner})
    await consumer_factory().poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", tool.client_id) == "deleted"
    r = await tool.post("/v1/integrations/handshake", {"installation": {"device_id": tool.device_id}})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED"
    assert await env.scalar("SELECT count(*) FROM integration_credentials WHERE status = 'active'") == 0
    assert await env.scalar("SELECT status FROM account_projection WHERE account_id = $1", tool.owner) == "deleted"


async def test_pending_client_stays_inactive_if_account_is_suspended_before_verifying(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc))
    client_id = (await env.rows("SELECT id FROM integration_clients"))
    fake_feed.add("account.status_changed", {"account_id": acc, "status": "suspended", "previous": "pending"})
    fake_feed.add("account.verified", {"account_id": acc})
    await consumer_factory().poll_once()
    cid = (await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", acc))[0]["id"]
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", cid) == "suspended"


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
    fake_feed.add("account.permission_changed", permission_changed(acc))
    async with app2.router.lifespan_context(app2):
        for _ in range(300):
            await asyncio.sleep(0.02)
            async with db.connection() as conn:
                if await conn.fetchval("SELECT count(*) FROM integration_clients WHERE owner_account_id = $1", acc):
                    break
        async with db.connection() as conn:
            assert await conn.fetchval(
                "SELECT status FROM integration_clients WHERE owner_account_id = $1", acc) == "pending_email_verification"
    assert ISSUER  # lifespan thoát không treo: các task nền đã bị huỷ


async def test_verified_event_never_activates_a_client_of_a_suspended_account_defense_in_depth(env, fake_feed, consumer_factory):
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc))
    c = consumer_factory()
    await c.poll_once()
    cid = (await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", acc))[0]["id"]
    await env.projection(acc, verified=False, status="suspended")   # khoá lệch pha với client
    fake_feed.add("account.verified", {"account_id": acc})
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", cid) == "pending_email_verification"


# ── account.permission_changed → auto-provision client ───────────────────────
# Khi capability:tool_integration được cấp, service tự tạo primary_tool client
# nếu account chưa có client nào. Kiểm tra idempotency và các trường hợp biên.

async def test_permission_changed_tool_integration_granted_creates_primary_tool_client(
        env, fake_feed, consumer_factory):
    """CẤP capability:tool_integration → tự tạo primary_tool client cho account."""
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc))
    await consumer_factory().poll_once()
    clients = await env.rows("SELECT * FROM integration_clients WHERE owner_account_id = $1", acc)
    assert len(clients) == 1
    client = clients[0]
    assert client["client_kind"] == "primary_tool" and client["ownership"] == "personal"
    assert client["status"] == "pending_email_verification"  # account chưa xác thực email
    assert client["created_from"] == "provisioning"
    cred = await env.rows("SELECT reveal_state FROM integration_credentials WHERE client_id = $1", client["id"])
    assert len(cred) == 1 and cred[0]["reveal_state"] == "pending"
    evs = await outbox(env, "integration.client.provisioned")
    assert evs[0]["data"]["status"] == "pending_email_verification"

    fake_feed.add("account.verified", {"account_id": acc})
    await consumer_factory().poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", client["id"]) == "active"
    evs = await outbox(env, "integration.client.provisioned")
    assert evs[-1]["data"]["status"] == "active"


async def test_permission_changed_unverified_account_creates_pending_client(
        env, fake_feed, consumer_factory):
    """Account chưa xác thực email → client ở trạng thái pending_email_verification."""
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc))
    await consumer_factory().poll_once()
    clients = await env.rows("SELECT status FROM integration_clients WHERE owner_account_id = $1", acc)
    assert len(clients) == 1
    assert clients[0]["status"] == "pending_email_verification"


async def test_permission_changed_idempotent_if_client_already_exists(
        env, fake_feed, consumer_factory):
    """Đã có client → sự kiện permission_changed không tạo thêm (idempotent)."""
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc))
    c = consumer_factory()
    await c.poll_once()
    client_id_before = (await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", acc))[0]["id"]
    fake_feed.add("account.permission_changed", permission_changed(acc))
    await c.poll_once()
    clients = await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", acc)
    assert len(clients) == 1, "Client cũ bị thay thế bằng client mới"
    assert clients[0]["id"] == client_id_before, "Client cũ bị thay thế bằng client mới"


async def test_permission_changed_other_permission_does_not_provision(
        env, fake_feed, consumer_factory):
    """Permission khác (reward:access) không kích hoạt auto-provision."""
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc, permission="reward:access"))
    await consumer_factory().poll_once()
    clients = await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", acc)
    assert len(clients) == 0, f"Không được tạo client cho reward:access, nhận {len(clients)}"


async def test_permission_changed_revoke_does_not_provision(
        env, fake_feed, consumer_factory):
    """Thu hồi capability (granted=False) không tạo client."""
    acc = uuid.uuid4().hex
    fake_feed.add("account.permission_changed", permission_changed(acc, granted=False))
    await consumer_factory().poll_once()
    clients = await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", acc)
    assert len(clients) == 0, f"Thu hồi không được tạo client, nhận {len(clients)}"


async def test_permission_changed_revoke_suspends_existing_client_and_revokes_credentials(
        env, fake_feed, consumer_factory):
    """Thu hồi capability trên account ĐÃ có client personal active => suspend + thu hồi mọi credential."""
    tool = await env.tool()
    c = consumer_factory()
    fake_feed.add("account.permission_changed", permission_changed(tool.owner, granted=False))
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", tool.client_id) == "suspended"
    assert await env.scalar("SELECT suspended_by FROM integration_clients WHERE id = $1", tool.client_id) == "capability"
    r = await tool.post("/v1/integrations/handshake", {"installation": {"device_id": tool.device_id}})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED"


async def test_permission_changed_regrant_reactivates_suspended_personal_client(
        env, fake_feed, consumer_factory):
    """Cấp lại capability sau khi đã thu hồi => kích hoạt lại CÙNG client personal (không tạo mới), cấp credential
    pending-reveal mới."""
    tool = await env.tool()
    old_client_id = tool.client_id
    c = consumer_factory()
    fake_feed.add("account.permission_changed", permission_changed(tool.owner, granted=False))
    await c.poll_once()
    assert await env.scalar("SELECT status FROM integration_clients WHERE id = $1", old_client_id) == "suspended"
    fake_feed.add("account.permission_changed", permission_changed(tool.owner, granted=True))
    await c.poll_once()
    clients = await env.rows("SELECT id, status FROM integration_clients WHERE owner_account_id = $1", tool.owner)
    assert len(clients) == 1 and clients[0]["id"] == old_client_id and clients[0]["status"] == "active"
    cred = await env.rows(
        "SELECT reveal_state FROM integration_credentials WHERE client_id = $1 AND status = 'active'", old_client_id)
    assert len(cred) == 1 and cred[0]["reveal_state"] == "pending"
