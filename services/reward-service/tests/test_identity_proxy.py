"""Proxy auth mỏng + client outbound tới Identity (timeout/backoff) + cô lập lỗi khi Identity chết (Phase 4)."""

from __future__ import annotations

import logging
import secrets
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reward_service.identity_link.client import IdentityClient, IdentityUnavailable  # noqa: E402
from tests.conftest import app_client, insert_account, sign_token  # noqa: E402

USER_ROUTE = "/v1/reward/credentials"


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Proxy mỏng: login/refresh/logout/me/validate ─────────────────────────────────

async def test_login_is_forwarded_to_identity_public_api_with_real_ip(client, identity):
    pw = secrets.token_urlsafe(12)
    user = identity.add_user(role="discord", username="proxy_user", password=pw)
    resp = await client.post("/v1/reward/auth/login", json={"username": "proxy_user", "password": pw},
                             headers={"X-Real-IP": "203.0.113.9", "X-Client-Type": "web"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["refresh_token"] == f"rt-{user['id']}" and body["access_token"]
    sent = identity.calls("POST", "/v1/auth/login")
    assert len(sent) == 1 and sent[0]["x_real_ip"] == "203.0.113.9" and sent[0]["x_client_type"] == "web"
    # Token nhận được dùng được ngay trên Reward (verify offline bằng JWKS)
    assert (await client.get(USER_ROUTE, headers=_hdr(body["access_token"]))).status_code == 200


async def test_login_error_from_identity_is_passed_through_unchanged(client, identity):
    identity.add_user(role="viewer", username="u_err", password=secrets.token_urlsafe(12))
    resp = await client.post("/v1/reward/auth/login", json={"username": "u_err", "password": "sai-mat-khau"})
    assert resp.status_code == 401 and resp.json() == {"detail": "Sai tên đăng nhập hoặc mật khẩu"}


async def test_refresh_and_logout_are_forwarded(client, identity):
    user = identity.add_user(role="discord")
    r1 = await client.post("/v1/reward/auth/refresh", json={"refresh_token": f"rt-{user['id']}"})
    assert r1.status_code == 200 and r1.json()["access_token"]
    r2 = await client.post("/v1/reward/auth/refresh", json={"refresh_token": "rt-khong-ton-tai"})
    assert r2.status_code == 401
    r3 = await client.post("/v1/reward/auth/logout", json={"refresh_token": f"rt-{user['id']}"})
    assert r3.status_code == 200 and r3.json() == {"ok": True}
    assert len(identity.calls("POST", "/v1/auth/refresh")) == 2 and len(identity.calls("POST", "/v1/auth/logout")) == 1


async def test_me_keeps_legacy_shape_without_database_lookup(client, identity):
    user = identity.add_user(role="discord", username="me_user")
    resp = await client.get("/v1/reward/auth/me", headers=identity.bearer(user))
    assert resp.status_code == 200
    assert resp.json() == {"id": user["id"], "username": "me_user", "role": "discord", "token_role": "discord",
                           "status": "active", "reward_access": True, "has_credential": False,
                           "credential_status": None, "discord_username": None}
    viewer = identity.add_user(role="viewer")
    assert (await client.get("/v1/reward/auth/me", headers=identity.bearer(viewer))).json()["reward_access"] is False


async def test_me_rejects_bad_token_without_calling_identity(client, identity):
    before = len(identity.calls("GET", "/v1/auth/me"))
    resp = await client.get("/v1/reward/auth/me", headers={"Authorization": "Bearer khong.phai.jwt"})
    assert resp.status_code == 401
    assert (await client.get("/v1/reward/auth/me")).status_code == 401
    assert len(identity.calls("GET", "/v1/auth/me")) == before


async def test_validate_diagnostic_uses_token_verifier(client, identity):
    user = identity.add_user(role="viewer", perms=("reward:access",))
    ok = (await client.post("/v1/reward/auth/validate", json={"token": identity.token(user)})).json()
    assert ok["valid"] is True and ok["reward_access"] is True and ok["claims"]["perms"] == ["reward:access"]
    assert ok["claims"]["sub"] == user["id"]
    expired = (await client.post("/v1/reward/auth/validate",
                                 json={"token": identity.token(user, exp=int(time.time()) - 600,
                                                               iat=int(time.time()) - 1200,
                                                               nbf=int(time.time()) - 1200)})).json()
    assert expired["valid"] is False and expired["reason"] == "Token hết hạn"
    bad = (await client.post("/v1/reward/auth/validate", json={"token": "rac.rac.rac"})).json()
    assert bad["valid"] is False and bad["claims"] is None
    assert (await client.post("/v1/reward/auth/validate", json={})).json()["reason"] == "Thiếu token"
    via_header = (await client.post("/v1/reward/auth/validate", json={}, headers=identity.bearer(user))).json()
    assert via_header["valid"] is True


async def test_openapi_marks_auth_proxy_routes_deprecated(client):
    spec = (await client.get("/openapi.json")).json()
    for path, method in (("/v1/reward/auth/login", "post"), ("/v1/reward/auth/refresh", "post"),
                         ("/v1/reward/auth/logout", "post"), ("/v1/reward/auth/me", "get")):
        op = spec["paths"][path][method]
        assert op["deprecated"] is True and "DEPRECATED" in op["summary"], path
    assert "deprecated" not in spec["paths"]["/v1/reward/jobs"]["get"]


async def test_no_password_or_token_is_ever_logged(client, identity, caplog):
    caplog.set_level(logging.DEBUG)
    password = "MK-" + secrets.token_urlsafe(18)  # ngẫu nhiên mỗi lần chạy
    user = identity.add_user(role="discord", username="log_user", password=password)
    login = (await client.post("/v1/reward/auth/login", json={"username": "log_user", "password": password})).json()
    await client.post("/v1/reward/auth/refresh", json={"refresh_token": login["refresh_token"]})
    await client.get("/v1/reward/auth/me", headers=_hdr(login["access_token"]))
    await client.post("/v1/reward/auth/logout", json={"refresh_token": login["refresh_token"]})
    await client.get("/v1/reward/auth/me", headers={"Authorization": "Bearer sai.sai.sai"})
    blob = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (password, login["access_token"], login["refresh_token"], identity.feed_key):
        assert secret not in blob
    assert user["id"]  # tài khoản thật đã được dùng


# ── Client outbound: timeout + retry/backoff có giới hạn ─────────────────────────

class _Counter:
    def __init__(self, plan):
        self.plan = list(plan)
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        step = self.plan.pop(0) if self.plan else self.plan_default
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, json={"ok": step < 400})

    plan_default = 200


def _client(plan, *, retries=2, sleeps=None):
    counter = _Counter(plan)

    async def fake_sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(counter.handler))
    return IdentityClient("http://identity.test", retries=retries, backoff_base=0.5, http_client=http,
                          sleep=fake_sleep), counter


async def test_get_retries_transport_errors_with_exponential_backoff_then_gives_up():
    sleeps: list[float] = []
    client, counter = _client([httpx.ConnectError("x")] * 10, retries=2, sleeps=sleeps)
    with pytest.raises(IdentityUnavailable):
        await client.request("GET", "/v1/admin/users")
    assert counter.calls == 3  # 1 + 2 retry — GIỚI HẠN, không vô hạn
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] * 1.2  # backoff mũ (jitter ±20%)


async def test_get_recovers_after_transient_5xx():
    client, counter = _client([503, 502, 200])
    resp = await client.request("GET", "/x")
    assert resp.status_code == 200 and counter.calls == 3


async def test_4xx_and_429_are_returned_not_retried():
    for status in (401, 403, 404, 409, 429):
        client, counter = _client([status])
        resp = await client.request("GET", "/x")
        assert resp.status_code == status and counter.calls == 1


async def test_post_is_not_replayed_after_ambiguous_timeout_but_is_after_connect_error():
    client, counter = _client([httpx.ReadTimeout("x")] * 5)
    with pytest.raises(IdentityUnavailable):
        await client.request("POST", "/v1/auth/login", json={})
    assert counter.calls == 1  # timeout đọc: Identity có thể đã xử lý ⇒ không gửi lại thao tác có tác dụng phụ
    client, counter = _client([httpx.ConnectError("x"), 200])
    assert (await client.request("POST", "/v1/auth/login", json={})).status_code == 200 and counter.calls == 2


async def test_put_delete_are_idempotent_and_retried_on_timeout():
    for method in ("PUT", "DELETE"):
        client, counter = _client([httpx.ReadTimeout("x"), 200])
        assert (await client.request(method, "/p")).status_code == 200 and counter.calls == 2


async def test_client_sends_bearer_and_real_ip_but_never_logs_them(caplog):
    caplog.set_level(logging.DEBUG)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = IdentityClient("http://identity.test", retries=0, http_client=http)
    with pytest.raises(IdentityUnavailable):
        await client.request("GET", "/v1/admin/users", authorization="Bearer BI-MAT-TOKEN-ZZZ", real_ip="1.2.3.4")
    assert seen["authorization"] == "Bearer BI-MAT-TOKEN-ZZZ" and seen["x-real-ip"] == "1.2.3.4"
    assert "BI-MAT-TOKEN-ZZZ" not in "\n".join(r.getMessage() for r in caplog.records)


async def test_account_id_in_permission_path_is_url_quoted():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.url.raw_path.decode()
        return httpx.Response(200, json={})

    client = IdentityClient("http://identity.test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.set_permission("Bearer x", "../../etc?x=1", "reward:access", grant=True)
    assert captured["raw"] == "/v1/admin/accounts/..%2F..%2Fetc%3Fx%3D1/permissions/reward:access"


# ── Identity chết / treo: lỗi rõ ràng, KHÔNG treo ───────────────────────────────

async def test_identity_dead_gives_503_unavailable_on_proxy_and_admin_routes(db_conn, identity):
    async with app_client(identity, REWARD_IDENTITY_TIMEOUT_S="0.5") as client:
        await _identity_dead_scenario(client, identity)


async def _identity_dead_scenario(client, identity):
    admin = identity.add_user(role="admin")
    target = identity.add_user(role="viewer")
    headers = identity.bearer(admin)
    assert (await client.get(USER_ROUTE, headers=identity.bearer(target))).status_code == 403  # nạp cache JWKS
    await client.get("/v1/reward/admin/role-audit", headers=headers)
    await identity.stop()

    started = time.monotonic()
    login = await client.post("/v1/reward/auth/login", json={"username": "x", "password": "y"})
    grant = await client.post(f"/v1/reward/admin/accounts/{target['id']}/grant-discord", json={}, headers=headers)
    revoke = await client.post(f"/v1/reward/admin/accounts/{target['id']}/revoke-discord", json={}, headers=headers)
    listing = await client.get("/v1/reward/admin/accounts", headers=headers)
    me = await client.get("/v1/reward/auth/me", headers=headers)
    assert time.monotonic() - started < 15  # 5 request × (2 lần thử × ≤0.5s) + backoff: có giới hạn, không treo
    for resp in (login, grant, revoke, listing, me):
        assert resp.status_code == 503, resp.text
        assert resp.json()["code"] == "UNAVAILABLE" and resp.json()["error"]["code"] == "UNAVAILABLE"
        assert resp.headers["retry-after"]
    # Audit riêng của Reward ghi nhận lần thử thất bại do Identity không sẵn sàng
    async with client.app_.state.pool.acquire() as conn:
        outcomes = [r["outcome"] for r in await conn.fetch(
            "SELECT outcome FROM reward_role_audit WHERE account_id = $1", target["id"])]
    assert outcomes == ["unavailable", "unavailable"]  # grant + revoke đều được ghi nhận, không có "applied" giả


async def test_identity_hanging_hits_short_timeout_and_returns_503(db_conn, identity):
    user = identity.add_user(role="viewer")
    async with app_client(identity, REWARD_IDENTITY_TIMEOUT_S="0.3", REWARD_IDENTITY_RETRIES="1") as ac:
        identity.hang_seconds = 2.5
        started = time.monotonic()
        resp = await ac.post("/v1/reward/auth/refresh", json={"refresh_token": f"rt-{user['id']}"})
        elapsed = time.monotonic() - started
        identity.hang_seconds = 0
    assert resp.status_code == 503 and resp.json()["code"] == "UNAVAILABLE"
    assert elapsed < 2.0  # timeout 0.3s × tối đa 2 lần + backoff, không chờ 2.5s của Identity


async def test_grant_resumes_when_identity_comes_back(client, db_conn, identity):
    admin = await insert_account(db_conn, role="admin")
    target = await insert_account(db_conn, role="viewer")
    headers = {"Authorization": f"Bearer {sign_token(admin)}"}
    await client.get("/v1/reward/admin/role-audit", headers=headers)  # nạp JWKS
    await identity.stop()
    assert (await client.post(f"/v1/reward/admin/accounts/{target['id']}/grant-discord", json={},
                              headers=headers)).status_code == 503
    await identity.start(identity.port)
    resp = await client.post(f"/v1/reward/admin/accounts/{target['id']}/grant-discord", json={}, headers=headers)
    assert resp.status_code == 200, resp.text


# ── Fault isolation ──────────────────────────────────────────────────────────────

async def test_requests_with_valid_token_are_served_while_identity_is_dead(client, db_conn, identity):
    """JWKS đã cache ⇒ Reward vẫn phục vụ token còn hạn (verify offline) và các route cục bộ khi Identity chết."""
    user = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    admin = await insert_account(db_conn, role="admin")
    token_user, token_admin = sign_token(user), sign_token(admin)
    assert (await client.get(USER_ROUTE, headers=_hdr(token_user))).status_code == 200  # nạp JWKS

    await identity.stop()
    assert (await client.get(USER_ROUTE, headers=_hdr(token_user))).status_code == 200
    assert (await client.get("/v1/reward/jobs", headers=_hdr(token_user))).status_code == 200
    assert (await client.get("/v1/reward/admin/jobs", headers=_hdr(token_admin))).status_code == 200
    assert (await client.get("/v1/reward/admin/role-audit", headers=_hdr(token_admin))).status_code == 200
    health = await client.get("/v1/reward/health")
    assert health.status_code == 200 and health.json()["ok"] is True  # health KHÔNG gọi Identity
    # token hết hạn / sai chữ ký vẫn bị từ chối đúng (không "mở cửa" vì Identity chết)
    assert (await client.get(USER_ROUTE, headers=_hdr("rac.rac.rac"))).status_code == 401


async def test_cold_start_with_identity_dead_returns_401_not_crash(db_conn, identity):
    """Identity chết NGAY KHI app khởi động (prefetch JWKS thất bại, cache rỗng) ⇒ 401 rõ ràng cho request cần xác thực;
    app vẫn sống và tự khỏi khi Identity sống lại."""
    user = identity.add_user(role="viewer", perms=("reward:access",))
    token = identity.token(user)
    await identity.stop()
    async with app_client(identity) as ac:
        assert (await ac.get(USER_ROUTE, headers=_hdr(token))).status_code == 401
        assert (await ac.get("/v1/reward/health")).status_code == 200
        await identity.start(identity.port)
        # JWKS lấy lại được sau khi Identity sống lại (giới hạn 1 lần/30s ⇒ chấp nhận 401 ngay lượt đầu, sau đó phải tự khỏi
        # khi qua khoảng nghỉ) — kiểm tra bằng cách giảm khoảng nghỉ của verifier.
        ac.app_.state.verifier._min_refetch = 0
        assert (await ac.get(USER_ROUTE, headers=_hdr(token))).status_code == 200
