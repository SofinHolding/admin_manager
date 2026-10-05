"""Admin API quản trị OIDC client + CLI seed-dev + CLI keys: validate URI, secret một lần, phân quyền, thu hồi khi xoá."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import TEST_FERNET_KEY, _dsn, _make_client, token_for
from oidc_util import (REDIRECT, authz_params, basic_header, exchange, get_code, make_account, pkce,
                       sso_login)

from identity_service.security import hash_token

pytestmark = pytest.mark.needs_pg

BASE = "/v1/admin/oidc/clients"
SERVICE_DIR = Path(__file__).resolve().parents[1]


async def admin(client, db, mailer, role="admin", name="quan_tri"):
    return (await token_for(client, db, mailer, username=name, role=role))["auth"]


def body(**kw) -> dict:
    return {"name": "Website B", "type": "public", "redirect_uris": [REDIRECT], **kw}


# ── phân quyền ──────────────────────────────────────────────────────────────────

async def test_only_admin_or_dash_admin_can_manage_clients(client, db, mailer):
    assert (await client.get(BASE)).status_code == 401
    viewer = await token_for(client, db, mailer, username="nguoi_xem")
    assert (await client.get(BASE, headers=viewer["auth"])).status_code == 403
    assert (await client.post(BASE, headers=viewer["auth"], json=body())).status_code == 403
    dash = await admin(client, db, mailer, "dash_admin", "dash")
    assert (await client.post(BASE, headers=dash, json=body())).status_code == 200


async def test_first_party_flag_is_admin_only(client, db, mailer):
    dash = await admin(client, db, mailer, "dash_admin", "dash")
    r = await client.post(BASE, headers=dash, json=body(first_party=True))
    assert r.status_code == 403
    adm = await admin(client, db, mailer)
    ok = await client.post(BASE, headers=adm, json=body(first_party=True))
    assert ok.status_code == 200 and ok.json()["first_party"] is True
    assert (await client.patch(f"{BASE}/{ok.json()['client_id']}", headers=dash,
                               json={"first_party": False})).status_code == 403


# ── tạo / secret một lần ───────────────────────────────────────────────────────

async def test_confidential_secret_is_shown_once_and_works(client, db, mailer):
    adm = await admin(client, db, mailer)
    r = await client.post(BASE, headers=adm, json=body(type="confidential", audience="web-b-api",
                                                       backchannel_logout_uri="https://b.example/logout",
                                                       allowed_scopes=["openid", "email"]))
    assert r.status_code == 200
    created = r.json()
    cid, secret = created["client_id"], created["client_secret"]
    assert cid.startswith("cid_") and len(secret) >= 40 and "secret_hash" not in created
    for url in (BASE, f"{BASE}/{cid}"):
        text = (await client.get(url, headers=adm)).text
        assert secret not in text and "client_secret" not in text and "secret_hash" not in text
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT secret_hash FROM oidc_clients WHERE client_id=$1", cid) == hash_token(secret)
    # secret dùng được thật ở token endpoint
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    code = await get_code(client, authz_params(cid, scope="openid"))
    assert (await exchange(client, code=code, verifier=None, client_id=cid, secret=secret)).status_code == 200
    assert (await client.get(f"{BASE}/{cid}", headers=adm)).json()["allowed_scopes"] == ["openid", "email"]


async def test_public_client_gets_no_secret(client, db, mailer):
    adm = await admin(client, db, mailer)
    created = (await client.post(BASE, headers=adm, json=body(client_id="spa-site"))).json()
    assert created["client_id"] == "spa-site" and "client_secret" not in created
    assert (await client.post(BASE, headers=adm, json=body(client_id="spa-site"))).status_code == 409
    assert (await client.patch(f"{BASE}/spa-site", headers=adm, json={"rotate_secret": True})).status_code == 422


@pytest.mark.parametrize("bad", [
    "https://*.example.com/cb", "https://app.example/*", "https://app.example/cb#frag", "javascript:alert(1)",
    "data:text/html,x", "ftp://app.example/cb", "http://app.example/cb", "http://evil.example/cb",
    "https://user:pw@app.example/cb", "https://app.example/c b", "app.example/cb", "//app.example/cb",
    "https://app.example/cb\nX-Evil: 1", "HTTPS://app.example/cb", "https://", "https:///cb",
    "https://app.example\\@evil.example/cb", "https://app%2eexample/cb", "https://ứng-dụng.example/cb", "",
    "https://app.example:99999/cb", "http://127.0.0.1.evil.example/cb", "http://localhost@evil.example/cb"])
async def test_invalid_redirect_uris_are_rejected(client, db, mailer, bad):
    adm = await admin(client, db, mailer)
    r = await client.post(BASE, headers=adm, json=body(redirect_uris=[REDIRECT, bad]))
    assert r.status_code == 422, bad
    assert bad not in r.text or bad == ""                  # lỗi validation không phản chiếu giá trị đầu vào


async def test_loopback_http_allowed_in_dev_but_not_in_production(client, db, mailer, settings, clock, sleeps):
    adm = await admin(client, db, mailer)
    for uri in ("http://127.0.0.1:5174/admin/callback", "http://localhost:3000/cb", "http://[::1]:8080/cb"):
        assert (await client.post(BASE, headers=adm, json=body(redirect_uris=[uri]))).status_code == 200, uri
    prod = replace(settings, env="production", issuer="https://identity.test")
    async with await _make_client(db, prod, mailer, clock, sleeps) as pclient:
        padm = (await token_for(pclient, db, mailer, username="quan_tri_p", role="admin"))["auth"]
        r = await pclient.post(BASE, headers=padm, json=body(redirect_uris=["http://127.0.0.1:5174/cb"]))
        assert r.status_code == 422
        assert (await pclient.post(BASE, headers=padm, json=body(redirect_uris=["https://app.example/cb"]))
                ).status_code == 200


@pytest.mark.parametrize("payload", [
    {"redirect_uris": []}, {"redirect_uris": "https://app.example/cb"}, {"redirect_uris": [REDIRECT] * 21 +
                                                                          [f"https://app.example/{i}" for i in range(25)]},
    {"type": "other"}, {"name": ""}, {"name": "x" * 201}, {"client_id": "a"}, {"client_id": "co dau cach"},
    {"allowed_scopes": []}, {"allowed_scopes": ["profile"]}, {"allowed_scopes": ["openid", "co dau cach"]},
    {"post_logout_redirect_uris": ["javascript:x"]}, {"backchannel_logout_uri": "http://evil.example/l"},
    {"backchannel_logout_uri": "https://*.example/l"}, {"audience": "co dau cach"}])
async def test_invalid_client_payloads_are_422(client, db, mailer, payload):
    adm = await admin(client, db, mailer)
    r = await client.post(BASE, headers=adm, json=body(**payload))
    assert r.status_code == 422, payload


# ── sửa / xoay secret / vô hiệu / xoá ─────────────────────────────────────────

async def test_rotate_secret_invalidates_the_old_one(client, db, mailer):
    adm = await admin(client, db, mailer)
    c = (await client.post(BASE, headers=adm, json=body(type="confidential"))).json()
    cid, old = c["client_id"], c["client_secret"]
    r = await client.patch(f"{BASE}/{cid}", headers=adm, json={"rotate_secret": True})
    new = r.json()["client_secret"]
    assert new != old
    data = {"grant_type": "authorization_code", "code": "x", "redirect_uri": REDIRECT}
    assert (await client.post("/oauth/token", headers=basic_header(cid, old), data=data)).status_code == 401
    assert (await client.post("/oauth/token", headers=basic_header(cid, new), data=data)).json()["error"] == "invalid_grant"
    assert "client_secret" not in (await client.get(f"{BASE}/{cid}", headers=adm)).text


async def test_patch_validates_and_rejects_unknown_fields(client, db, mailer):
    adm = await admin(client, db, mailer)
    cid = (await client.post(BASE, headers=adm, json=body())).json()["client_id"]
    assert (await client.patch(f"{BASE}/{cid}", headers=adm, json={"redirect_uris": ["https://*/x"]})).status_code == 422
    assert (await client.patch(f"{BASE}/{cid}", headers=adm, json={"type": "confidential"})).status_code == 422
    assert (await client.patch(f"{BASE}/{cid}", headers=adm, json={"secret_hash": "x"})).status_code == 422
    assert (await client.patch(f"{BASE}/{cid}", headers=adm, json={"status": "bad"})).status_code == 422
    assert (await client.patch(f"{BASE}/khong-co", headers=adm, json={"name": "x"})).status_code == 404
    ok = await client.patch(f"{BASE}/{cid}", headers=adm, json={"name": "Ten moi", "redirect_uris": ["https://b.example/cb"]})
    assert ok.json()["name"] == "Ten moi" and ok.json()["redirect_uris"] == ["https://b.example/cb"]
    # redirect_uri cũ không còn hợp lệ ở authorize
    r = await client.get("/oauth/authorize", params=authz_params(cid, challenge=pkce()[1]))
    assert r.status_code == 400 and "location" not in r.headers


async def test_disabling_a_client_blocks_authorize_and_token(client, db, mailer):
    adm = await admin(client, db, mailer)
    cid = (await client.post(BASE, headers=adm, json=body())).json()["client_id"]
    await client.patch(f"{BASE}/{cid}", headers=adm, json={"status": "disabled"})
    assert (await client.get("/oauth/authorize", params=authz_params(cid, challenge=pkce()[1]))).status_code == 400
    r = await exchange(client, code="x", verifier=pkce()[0], client_id=cid)
    assert (r.status_code, r.json()["error"]) == (401, "invalid_client")


async def test_deleting_a_client_revokes_its_sessions_and_removes_it(client, db, mailer):
    adm = await admin(client, db, mailer)
    cid = (await client.post(BASE, headers=adm, json=body())).json()["client_id"]
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(cid, challenge=ch))
    t = (await exchange(client, code=code, verifier=v, client_id=cid)).json()
    assert (await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {t['access_token']}"})).status_code == 200
    assert (await client.delete(f"{BASE}/{cid}", headers=adm)).json() == {"ok": True}
    assert (await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {t['access_token']}"})).status_code == 401
    assert (await client.get(f"{BASE}/{cid}", headers=adm)).status_code == 404
    assert (await client.delete(f"{BASE}/{cid}", headers=adm)).status_code == 404
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM auth_audit WHERE event_type LIKE 'oidc_client_%'") >= 2


# ── CLI ─────────────────────────────────────────────────────────────────────────

def run_cli(module: str, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    root = SERVICE_DIR.parents[1]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "IDENTITY_DATABASE_URL": _dsn(),
           "IDENTITY_KEY_ENCRYPTION_KEY": TEST_FERNET_KEY, "IDENTITY_ENV": "test",
           "PYTHONPATH": os.pathsep.join(str(p) for p in (SERVICE_DIR / "src", root / "packages" / "observability" / "src",
                                                          root / "packages" / "contracts" / "python"))}
    env.pop("IDENTITY_WEB_REDIRECT_URIS", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", module, *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)


async def test_seed_dev_creates_manager_web_with_default_redirect_only(client, db):
    r = run_cli("identity_service.oidc_clients", "seed-dev")
    assert r.returncode == 0, r.stderr
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM oidc_clients WHERE client_id='manager-web'")
    assert (row["type"], row["first_party"], row["secret_hash"], row["status"]) == ("public", True, "", "active")
    assert list(row["redirect_uris"]) == ["http://127.0.0.1:5174/admin/callback"]
    again = run_cli("identity_service.oidc_clients", "seed-dev")
    assert again.returncode == 0 and "Đã cập nhật" in again.stdout


async def test_seed_dev_reads_redirects_from_env_and_validates(client, db):
    r = run_cli("identity_service.oidc_clients", "seed-dev",
                env_extra={"IDENTITY_WEB_REDIRECT_URIS": "http://127.0.0.1:6000/cb, https://admin.example/cb"})
    assert r.returncode == 0, r.stderr
    async with db.connection() as conn:
        uris = list(await conn.fetchval("SELECT redirect_uris FROM oidc_clients WHERE client_id='manager-web'"))
    assert uris == ["http://127.0.0.1:6000/cb", "https://admin.example/cb"]
    bad = run_cli("identity_service.oidc_clients", "seed-dev", env_extra={"IDENTITY_WEB_REDIRECT_URIS": "https://*.x/cb"})
    assert bad.returncode == 2
    prod = run_cli("identity_service.oidc_clients", "seed-dev", env_extra={"IDENTITY_ENV": "production",
                                                                          "IDENTITY_ISSUER": "https://idp.example"})
    assert prod.returncode == 2 and "production" in prod.stderr
    assert run_cli("identity_service.oidc_clients", "lam-gi-do").returncode == 2


async def test_seeded_manager_web_completes_pkce_flow(client, db):
    run_cli("identity_service.oidc_clients", "seed-dev")
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    uri = "http://127.0.0.1:5174/admin/callback"
    code = await get_code(client, authz_params("manager-web", challenge=ch, redirect_uri=uri,
                                               scope="openid profile email offline_access"))
    r = await exchange(client, code=code, verifier=v, client_id="manager-web", redirect_uri=uri)
    assert r.status_code == 200 and r.json()["refresh_token"]


async def test_keys_cli_rotate_publishes_both_keys_in_jwks(client, db):
    before = {k["kid"] for k in (await client.get("/.well-known/jwks.json")).json()["keys"]}
    assert len(before) == 1
    try:
        r = run_cli("identity_service.keys", "rotate")
        assert r.returncode == 0, r.stderr
        new_kid = r.stdout.split(":")[-1].strip()
        listing = run_cli("identity_service.keys", "list")
        assert listing.returncode == 0 and set(listing.stdout.split()) == before | {new_kid}
        after = {k["kid"] for k in (await client.get("/.well-known/jwks.json")).json()["keys"]}
        assert after == before | {new_kid}                       # khoá cũ vẫn publish trong thời gian ân hạn
        assert run_cli("identity_service.keys", "bay-gio").returncode == 2
    finally:  # signing_keys không nằm trong TRUNCATE chung: dọn để test khác thấy đúng một khoá
        async with db.connection() as conn:
            await conn.execute("TRUNCATE signing_keys")
