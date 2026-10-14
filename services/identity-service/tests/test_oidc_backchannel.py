"""Back-channel logout: receiver cục bộ THẬT xác minh logout_token bằng JWKS; retry/backoff/dead-letter; SSRF; cô lập."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import replace

import jwt
import pytest
from conftest import ISSUER, token_for
from http_fixtures import Receiver, dead_port_url
from oidc_util import (authz_params, exchange, jwks_key, make_account, make_client, pkce, get_code, sso_login)

from identity_service.oidc.backchannel import LOGOUT_EVENT, BackchannelWorker
from identity_service.oidc.uri import UnsafeTarget, check_ip, resolve_safe

pytestmark = pytest.mark.needs_pg


@pytest.fixture
def receivers():
    made: list[Receiver] = []

    def make(**kw) -> Receiver:
        r = Receiver(**kw)
        made.append(r)
        return r
    yield make
    for r in made:
        r.stop()


def worker_of(client) -> BackchannelWorker:
    return client.app.state.container.backchannel


async def login_to(client, db, username: str, client_id: str, *, backchannel: str, first=True) -> dict:
    """Đăng nhập SSO (nếu cần) rồi lấy token cho `client_id` (public, PKCE)."""
    await make_client(db, client_id, backchannel=backchannel, audience=f"{client_id}-api")
    if first:
        await make_account(db, username)
    await sso_login(client, username)
    v, ch = pkce()
    code = await get_code(client, authz_params(client_id, challenge=ch, scope="openid offline_access"))
    r = await exchange(client, code=code, verifier=v, client_id=client_id)
    assert r.status_code == 200, r.text
    return r.json()


async def verify_logout_token(client, raw: str, *, client_id: str) -> dict:
    key = await jwks_key(client, raw)
    header = jwt.get_unverified_header(raw)
    assert header["alg"] == "RS256" and header["typ"] == "logout+jwt"
    claims = jwt.decode(raw, key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                        options={"require": ["iss", "aud", "iat", "jti", "events"], "verify_iat": False})
    assert LOGOUT_EVENT in claims["events"] and claims["events"][LOGOUT_EVENT] == {}
    assert "nonce" not in claims                                   # OIDC Back-Channel Logout §2.4
    assert claims.get("sub") or claims.get("sid")
    return claims


# ── giao hàng thành công, xác minh bằng JWKS ──────────────────────────────────

async def test_logout_sends_verifiable_logout_token_to_each_relying_party(client, db, receivers):
    rx_a, rx_b = receivers(), receivers()
    ta = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx_a.url)
    tb = await login_to(client, db, "nguoi_dung", "rp-bbb", backchannel=rx_b.url, first=False)
    await client.get("/oauth/logout", params={"id_token_hint": ta["id_token"]})
    stats = await worker_of(client).run_once()
    assert stats["delivered"] == 2 and stats["enqueued"] == 2
    for rx, tok, cid in ((rx_a, ta, "rp-aaa"), (rx_b, tb, "rp-bbb")):
        assert len(rx.requests) == 1
        req = rx.requests[0]
        assert req["method"] == "POST" and req["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert req["headers"]["Cache-Control"] == "no-store"
        claims = await verify_logout_token(client, req["form"]["logout_token"], client_id=cid)
        id_claims = jwt.decode(tok["id_token"], options={"verify_signature": False})
        assert claims["sid"] == id_claims["sid"] and claims["sub"] == id_claims["sub"]
        assert claims["exp"] - claims["iat"] <= 120
    jtis = {(await verify_logout_token(client, rx.requests[0]["form"]["logout_token"], client_id=cid))["jti"]
            for rx, cid in ((rx_a, "rp-aaa"), (rx_b, "rp-bbb"))}
    assert len(jtis) == 2                                          # jti khác nhau (chống replay)


async def test_logout_token_for_one_rp_is_rejected_when_audience_is_another(client, db, receivers):
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx.url)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    await worker_of(client).run_once()
    raw = rx.requests[0]["form"]["logout_token"]
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(raw, await jwks_key(client, raw), algorithms=["RS256"], audience="rp-bbb", issuer=ISSUER,
                   options={"verify_iat": False})


async def test_worker_is_idempotent_one_delivery_per_client_and_sid(client, db, receivers):
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx.url)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    w = worker_of(client)
    await w.run_once()
    await w.run_once()
    await w.run_once()
    assert len(rx.requests) == 1
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT seq FROM oidc_cursor WHERE name='backchannel'") == \
            await conn.fetchval("SELECT max(seq) FROM outbox WHERE type='session.revoked'")


async def test_suspending_the_account_sends_back_channel_logout(client, db, receivers, mailer):
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx.url)
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    uid = jwt.decode(t["id_token"], options={"verify_signature": False})["sub"]
    await client.patch(f"/v1/admin/users/{uid}/status", headers=admin["auth"], json={"status": "suspended"})
    await worker_of(client).run_once()
    assert len(rx.requests) == 1
    claims = await verify_logout_token(client, rx.requests[0]["form"]["logout_token"], client_id="rp-aaa")
    assert claims["sub"] == uid


async def test_deleting_the_account_still_notifies_relying_parties(client, db, receivers, mailer):
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx.url)
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    uid = jwt.decode(t["id_token"], options={"verify_signature": False})["sub"]
    assert (await client.delete(f"/v1/admin/users/{uid}", headers=admin["auth"])).status_code == 200
    await worker_of(client).run_once()
    assert len(rx.requests) == 1


async def test_clients_without_uri_or_disabled_get_nothing(client, db, receivers):
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel="")
    await login_to(client, db, "nguoi_dung", "rp-bbb", backchannel=rx.url, first=False)
    async with db.connection() as conn:
        await conn.execute("UPDATE oidc_clients SET status='disabled' WHERE client_id='rp-bbb'")
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    stats = await worker_of(client).run_once()
    assert stats["enqueued"] == 0 and rx.requests == []


# ── receiver chết / lỗi: retry, backoff, dead-letter, cô lập ───────────────────

async def test_dead_receiver_does_not_crash_or_block_other_clients_and_retries_with_backoff(
        client, db, receivers, clock):
    good = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-good", backchannel=good.url)
    await login_to(client, db, "nguoi_dung", "rp-dead", backchannel=dead_port_url(), first=False)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    w = worker_of(client)
    stats = await w.run_once()
    assert stats["delivered"] == 1 and stats["retry"] == 1 and len(good.requests) == 1
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM oidc_backchannel_deliveries WHERE client_id='rp-dead'")
        assert (row["status"], row["attempts"]) == ("pending", 1) and row["last_error"]
        assert row["next_attempt_at"] > clock()                       # backoff: chưa đến hạn thử lại
    again = await w.run_once()                                         # chưa đến hạn => không thử
    assert again["retry"] == 0 and again["delivered"] == 0
    clock.advance(seconds=3600)
    assert (await w.run_once())["retry"] == 1                          # đến hạn => thử lại, vẫn lỗi


async def test_delivery_goes_to_dead_letter_after_max_attempts(client, db, settings, clock):
    t = await login_to(client, db, "nguoi_dung", "rp-dead", backchannel=dead_port_url())
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    w = BackchannelWorker(db, replace(settings, backchannel_max_attempts=3), client.app.state.container.tokens,
                          now=clock)
    seen = []
    for _ in range(5):
        seen.append(await w.run_once())
        clock.advance(seconds=3600)
    assert [s["retry"] for s in seen[:2]] == [1, 1] and seen[2]["dead"] == 1
    assert sum(s["retry"] + s["dead"] for s in seen[3:]) == 0         # đã dead: không thử nữa
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT status, attempts FROM oidc_backchannel_deliveries")
        assert (row["status"], row["attempts"]) == ("dead", 3)


async def test_receiver_recovering_later_gets_the_notification(client, db, receivers, clock):
    rx = receivers(mode="fail")
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx.url)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    w = worker_of(client)
    assert (await w.run_once())["retry"] == 1                          # 500 => thử lại sau
    rx.mode = "ok"
    clock.advance(seconds=3600)
    assert (await w.run_once())["delivered"] == 1
    assert len(rx.requests) == 2


async def test_slow_receiver_times_out_without_blocking_fast_one(client, db, receivers, settings, clock):
    slow, fast = receivers(delay=3.0), receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-slow", backchannel=slow.url)
    await login_to(client, db, "nguoi_dung", "rp-fast", backchannel=fast.url, first=False)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    w = BackchannelWorker(db, replace(settings, backchannel_timeout_s=0.5), client.app.state.container.tokens,
                          now=clock)
    start = time.monotonic()
    stats = await w.run_once()
    assert time.monotonic() - start < 2.5                              # không chờ receiver chậm đến 3s
    assert stats["delivered"] == 1 and stats["retry"] == 1 and len(fast.requests) == 1


async def test_redirects_are_not_followed(client, db, receivers):
    target = receivers()
    redirecting = receivers(mode="redirect", redirect_to=target.url)
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=redirecting.url)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    stats = await worker_of(client).run_once()
    assert stats["retry"] == 1 and stats["delivered"] == 0
    assert len(redirecting.requests) == 1 and target.requests == []   # KHÔNG theo chuyển hướng


async def test_run_forever_survives_database_failure_and_can_be_cancelled(settings, clock):
    from identity_service.db import Database
    from identity_service.tokens.service import TokenService

    broken = Database("postgresql://x:y@127.0.0.1:1/z", connect_timeout=0.2, acquire_timeout=0.2)
    w = BackchannelWorker(broken, replace(settings, backchannel_interval_s=0.05), TokenService(settings, None),
                          now=clock)
    task = asyncio.create_task(w.run_forever())
    await asyncio.sleep(0.4)
    assert not task.done()                                            # lỗi DB không làm task chết
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── SSRF ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ip,env,allow_private,ok", [
    ("169.254.169.254", "dev", False, False),      # metadata cloud
    ("169.254.1.1", "test", True, False),          # link-local, kể cả khi cho phép mạng nội bộ
    ("fe80::1", "dev", True, False),
    ("0.0.0.0", "dev", True, False),                # unspecified
    ("::", "dev", True, False),
    ("::ffff:169.254.169.254", "dev", True, False), # IPv4-mapped không lách được
    ("224.0.0.1", "dev", True, False),              # multicast
    ("127.0.0.1", "production", True, False),       # loopback ngoài dev
    ("::1", "production", False, False),
    ("127.0.0.1", "dev", False, True),
    ("::1", "test", False, True),
    ("10.0.0.5", "dev", False, False),              # RFC1918 mặc định bị chặn
    ("192.168.1.1", "production", False, False),
    ("10.0.0.5", "production", True, True),         # chỉ khi cấu hình cho phép
    ("100.64.0.1", "production", False, False),     # CGNAT
    ("93.184.216.34", "production", False, True),   # IP công khai
])
def test_check_ip_policy(ip, env, allow_private, ok):
    addr = ipaddress.ip_address(ip)
    if ok:
        check_ip(addr, env=env, allow_private=allow_private)
    else:
        with pytest.raises(UnsafeTarget):
            check_ip(addr, env=env, allow_private=allow_private)


async def test_resolve_safe_rejects_bad_schemes_and_hosts():
    for url in ("http://example.com/x",                    # http tới host không phải loopback
                "ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)",
                "https://user:pass@example.com/x",          # userinfo
                "https://169.254.169.254/latest/meta-data", "http://169.254.169.254/",
                "http://0.0.0.0:80/", "https://[::]/", "https://[fe80::1]/", "http://[::ffff:169.254.169.254]/",
                "https://example.com/x#frag", "https://exa mple.com/"):
        with pytest.raises(UnsafeTarget):
            await resolve_safe(url, env="dev", allow_private=False)
    target = await resolve_safe("http://127.0.0.1:8080/x?y=1", env="dev", allow_private=False)
    assert (target.ip, target.port, target.path_query) == ("127.0.0.1", 8080, "/x?y=1")
    with pytest.raises(UnsafeTarget):
        await resolve_safe("http://127.0.0.1:8080/x", env="production", allow_private=False)
    with pytest.raises(UnsafeTarget):                      # DNS: localhost => loopback bị cấm ở production
        await resolve_safe("https://localhost/x", env="production", allow_private=False)


async def test_worker_never_connects_to_blocked_targets_and_dead_letters_them(client, db, receivers, settings, clock):
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-meta", backchannel="http://169.254.169.254/latest")
    await login_to(client, db, "nguoi_dung", "rp-zero", backchannel="http://0.0.0.0:9/x", first=False)
    await login_to(client, db, "nguoi_dung", "rp-ok", backchannel=rx.url, first=False)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    stats = await worker_of(client).run_once()
    assert stats["dead"] == 2 and stats["delivered"] == 1
    async with db.connection() as conn:
        errors = {r["client_id"]: r["last_error"] for r in await conn.fetch(
            "SELECT client_id, last_error FROM oidc_backchannel_deliveries WHERE status='dead'")}
    assert set(errors) == {"rp-meta", "rp-zero"} and all(e.startswith("ssrf_blocked") for e in errors.values())


async def test_loopback_receiver_is_refused_in_production_env(client, db, receivers, settings, clock):
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx.url)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    prod = BackchannelWorker(db, replace(settings, env="production"), client.app.state.container.tokens, now=clock)
    stats = await prod.run_once()
    assert stats["dead"] == 1 and rx.requests == []


# ── logout_token không rò rỉ vào log ────────────────────────────────────────────

async def test_logout_token_is_never_logged(client, db, receivers, caplog):
    caplog.set_level(logging.DEBUG)
    rx = receivers()
    t = await login_to(client, db, "nguoi_dung", "rp-aaa", backchannel=rx.url)
    await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    await worker_of(client).run_once()
    raw = rx.requests[0]["form"]["logout_token"]
    # chỉ xét log do SERVICE tạo (httpx của chính test in URL request của test, không phải của service)
    service_log = " | ".join(r.getMessage() for r in caplog.records if r.name.startswith("identity"))
    assert raw not in service_log and t["id_token"] not in service_log and t["access_token"] not in service_log
