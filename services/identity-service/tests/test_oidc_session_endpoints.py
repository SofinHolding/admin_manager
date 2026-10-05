"""userinfo, introspection (RFC 7662), revoke (RFC 7009), RP-initiated logout, thu hồi nhất quán với /v1/auth/*."""

from __future__ import annotations

import re
import time
from dataclasses import replace

import jwt
import pytest
from conftest import _make_client
from oidc_util import (authz_params, basic_header, exchange, full_login, get_code, make_account,
                       make_client, pkce, sso_login)

pytestmark = pytest.mark.needs_pg

POST_LOGOUT = "https://app.example/bye"


def bearer(t: dict) -> dict:
    return {"Authorization": f"Bearer {t['access_token']}"}


async def _rp(db, client_id="rp-main", audience="rp-api", **kw):
    """Một confidential client (RP backend) + secret."""
    secret = await make_client(db, client_id, ctype="confidential", audience=audience, **kw)
    return client_id, secret


async def introspect(client, cid, secret, token, **extra):
    return await client.post("/oauth/introspect", headers=basic_header(cid, secret), data={"token": token, **extra})


async def rp_login(client, db, username="nguoi_dung", scope="openid profile email offline_access"):
    cid, secret = await _rp(db)
    if not await _exists(db, username):
        await make_account(db, username)
    await sso_login(client, username)
    code = await get_code(client, authz_params(cid, scope=scope))
    r = await exchange(client, code=code, verifier=None, client_id=cid, secret=secret)
    assert r.status_code == 200, r.text
    return cid, secret, r.json()


async def _exists(db, username):
    async with db.connection() as conn:
        return bool(await conn.fetchval("SELECT 1 FROM accounts WHERE username=$1", username))


# ── userinfo ────────────────────────────────────────────────────────────────────

async def test_userinfo_returns_claims_by_scope_with_role_and_perms(client, db):
    await make_client(db)
    await make_account(db, "nguoi_dung")
    async with db.connection() as conn:
        await conn.execute("INSERT INTO account_permissions (account_id, permission) "
                           "SELECT id, 'reward:access' FROM accounts")
    full = await full_login(client, db, "nguoi_dung", scope="openid profile email")
    r = await client.get("/oauth/userinfo", headers=bearer(full))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    info = r.json()
    assert info["preferred_username"] == "nguoi_dung" and info["email"] == "nguoi_dung@example.com"
    assert info["email_verified"] is True and info["role"] == "viewer" and info["perms"] == ["reward:access"]
    assert info["sub"] == jwt.decode(full["access_token"], options={"verify_signature": False})["sub"]
    assert (await client.post("/oauth/userinfo", headers=bearer(full))).json() == info   # POST cũng được


async def test_userinfo_claims_follow_granted_scopes(client, db):
    await make_client(db)
    await make_account(db, "nguoi_dung")
    only_openid = await full_login(client, db, "nguoi_dung", scope="openid")
    info = (await client.get("/oauth/userinfo", headers=bearer(only_openid))).json()
    assert "email" not in info and "preferred_username" not in info and info["sub"]
    prof = await full_login(client, db, "nguoi_dung", scope="openid profile")
    info = (await client.get("/oauth/userinfo", headers=bearer(prof))).json()
    assert "preferred_username" in info and "email" not in info


async def test_userinfo_rejects_missing_invalid_or_non_oidc_tokens(client, db):
    await make_client(db)
    await make_account(db, "nguoi_dung")
    r = await client.get("/oauth/userinfo")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    r = await client.get("/oauth/userinfo", headers={"Authorization": "Bearer abc.def.ghi"})
    assert (r.status_code, r.json()["error"]) == (401, "invalid_token")
    full = await full_login(client, db, "nguoi_dung")
    assert (await client.get("/oauth/userinfo", params={"access_token": full["access_token"]})).status_code == 401
    # Token đăng nhập password (/v1/auth/login) không mang scope openid => 403 insufficient_scope
    login = (await client.post("/v1/auth/login", json={"username": "nguoi_dung", "password": "mat-khau-12345"})).json()
    r = await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {login['access_token']}"})
    assert (r.status_code, r.json()["error"]) == (403, "insufficient_scope")


async def test_userinfo_dies_when_session_is_revoked_via_legacy_logout_of_account_actions(client, db):
    await make_client(db)
    aid = await make_account(db, "nguoi_dung")
    full = await full_login(client, db, "nguoi_dung")
    assert (await client.get("/oauth/userinfo", headers=bearer(full))).status_code == 200
    async with db.connection() as conn:
        await conn.execute("UPDATE sessions SET revoked_at=now() WHERE account_id=$1 AND client_type='oidc'", aid)
    assert (await client.get("/oauth/userinfo", headers=bearer(full))).status_code == 401


# ── introspection ──────────────────────────────────────────────────────────────

async def test_introspection_requires_client_authentication(client, db):
    cid, secret, t = await rp_login(client, db)
    r = await client.post("/oauth/introspect", data={"token": t["access_token"]})
    assert (r.status_code, r.json()["error"]) == (401, "invalid_client")
    r = await client.post("/oauth/introspect", headers=basic_header(cid, "sai"), data={"token": t["access_token"]})
    assert (r.status_code, r.json()["error"]) == (401, "invalid_client")
    r = await client.post("/oauth/introspect", data={"token": t["access_token"], "client_id": cid})
    assert r.status_code == 401
    await make_client(db, "pub")                                          # public client không được introspect
    r = await client.post("/oauth/introspect", data={"token": t["access_token"], "client_id": "pub"})
    assert (r.status_code, r.json()["error"]) == (401, "invalid_client")
    # client_secret_post cũng được
    ok = await client.post("/oauth/introspect", data={"token": t["access_token"], "client_id": cid,
                                                      "client_secret": secret})
    assert ok.json()["active"] is True


async def test_introspection_active_token_has_standard_fields(client, db):
    cid, secret, t = await rp_login(client, db)
    r = await introspect(client, cid, secret, t["access_token"], token_type_hint="access_token")
    body = r.json()
    assert r.headers["cache-control"] == "no-store"
    assert body["active"] is True and body["client_id"] == cid and body["username"] == "nguoi_dung"
    assert body["token_type"] == "Bearer" and body["iss"] == "http://identity.test" and "rp-api" in body["aud"]
    assert body["scope"] == "openid profile email offline_access" and body["role"] == "viewer"
    assert body["sub"] and body["sid"] and body["exp"] > time.time()


async def test_introspection_garbage_unknown_and_other_clients_tokens_are_inactive(client, db):
    cid, secret, t = await rp_login(client, db)
    for token in ("", "rac", "a.b.c", "x" * 9000, t["access_token"] + "x"):
        assert (await introspect(client, cid, secret, token)).json() == {"active": False}
    other_id, other_secret = await _rp(db, "rp-two", audience="rp2-api")
    # token cấp cho client `rp` KHÔNG được client `rp2` hỏi
    assert (await introspect(client, other_id, other_secret, t["access_token"])).json() == {"active": False}


async def test_introspection_expired_token_is_inactive(db, settings, mailer, clock, sleeps):
    expired = replace(settings, access_token_ttl_s=-120)
    async with await _make_client(db, expired, mailer, clock, sleeps) as client:
        cid, secret, t = await rp_login(client, db)
        assert (await introspect(client, cid, secret, t["access_token"])).json() == {"active": False}


async def test_introspection_becomes_inactive_after_rp_initiated_logout(client, db):
    cid, secret, t = await rp_login(client, db)
    assert (await introspect(client, cid, secret, t["access_token"])).json()["active"] is True
    r = await client.get("/oauth/logout", params={"id_token_hint": t["id_token"]})
    assert r.status_code == 200
    assert (await introspect(client, cid, secret, t["access_token"])).json() == {"active": False}
    assert (await introspect(client, cid, secret, t["refresh_token"], token_type_hint="refresh_token")).json() == {
        "active": False}


async def test_introspection_inactive_after_suspend_delete_role_change_and_password_reset(client, db, mailer):
    from conftest import token_for

    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    cid, secret = await _rp(db)
    outcomes = {}
    for action in ("suspend", "delete", "role", "reset"):
        name = f"u_{action}"
        aid = await make_account(db, name)
        await sso_login(client, name)
        code = await get_code(client, authz_params(cid, scope="openid offline_access"))
        t = (await exchange(client, code=code, verifier=None, client_id=cid, secret=secret)).json()
        assert (await introspect(client, cid, secret, t["access_token"])).json()["active"] is True
        if action == "suspend":
            await client.patch(f"/v1/admin/users/{aid}/status", headers=admin["auth"], json={"status": "suspended"})
        elif action == "delete":
            await client.delete(f"/v1/admin/users/{aid}", headers=admin["auth"])
        elif action == "role":
            await client.patch(f"/v1/admin/users/{aid}", headers=admin["auth"], json={"role": "dash_admin"})
        else:
            await client.post("/v1/auth/forgot-password", json={"email": f"{name}@example.com"})
            tok = mailer.last("reset")
            await client.post("/v1/auth/reset-password", json={"token": tok, "new_password": "mat-khau-moi-999"})
        outcomes[action] = ((await introspect(client, cid, secret, t["access_token"])).json()["active"],
                            (await introspect(client, cid, secret, t["refresh_token"])).json()["active"])
        client.cookies.clear()
    assert outcomes == {a: (False, False) for a in ("suspend", "delete", "role", "reset")}


async def test_introspection_detects_role_drift_even_if_session_row_still_alive(client, db):
    cid, secret, t = await rp_login(client, db)
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET role='dash_admin'")   # đổi role mà KHÔNG thu hồi phiên
    assert (await introspect(client, cid, secret, t["access_token"])).json() == {"active": False}


async def test_introspect_refresh_token_active_and_bound_to_client(client, db):
    cid, secret, t = await rp_login(client, db)
    r = (await introspect(client, cid, secret, t["refresh_token"], token_type_hint="refresh_token")).json()
    assert r["active"] is True and r["token_type"] == "refresh_token" and r["client_id"] == cid
    other_id, other_secret = await _rp(db, "rp-two", audience="rp2-api")
    assert (await introspect(client, other_id, other_secret, t["refresh_token"])).json() == {"active": False}


# ── revoke (RFC 7009) ──────────────────────────────────────────────────────────

async def test_revoke_refresh_token_kills_family_and_session(client, db):
    cid, secret, t = await rp_login(client, db)
    r = await client.post("/oauth/revoke", headers=basic_header(cid, secret),
                          data={"token": t["refresh_token"], "token_type_hint": "refresh_token"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    rf = await client.post("/oauth/token", headers=basic_header(cid, secret),
                           data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    assert rf.json()["error"] == "invalid_grant"
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 401


async def test_revoke_access_token_ends_the_grant(client, db):
    cid, secret, t = await rp_login(client, db)
    r = await client.post("/oauth/revoke", headers=basic_header(cid, secret), data={"token": t["access_token"]})
    assert r.status_code == 200
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 401
    assert (await introspect(client, cid, secret, t["refresh_token"])).json() == {"active": False}


async def test_revoke_unknown_token_is_200_and_other_clients_token_is_ignored(client, db):
    cid, secret, t = await rp_login(client, db)
    other_id, other_secret = await _rp(db, "rp-two", audience="rp2-api")
    for tok in ("khong-co", "a.b.c", ""):
        r = await client.post("/oauth/revoke", headers=basic_header(other_id, other_secret), data={"token": tok})
        assert r.status_code == 200
    await client.post("/oauth/revoke", headers=basic_header(other_id, other_secret), data={"token": t["refresh_token"]})
    await client.post("/oauth/revoke", headers=basic_header(other_id, other_secret), data={"token": t["access_token"]})
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 200   # không bị client khác huỷ


async def test_revoke_requires_client_authentication_and_token_param(client, db):
    cid, secret, t = await rp_login(client, db)
    assert (await client.post("/oauth/revoke", data={"token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/oauth/revoke", headers=basic_header(cid, "sai"),
                              data={"token": t["refresh_token"]})).status_code == 401
    r = await client.post("/oauth/revoke", headers=basic_header(cid, secret), data={})
    assert r.json()["error"] == "invalid_request"
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 200


async def test_public_client_can_revoke_its_own_refresh_token(client, db):
    await make_client(db)
    await make_account(db, "nguoi_dung")
    t = await full_login(client, db, "nguoi_dung", scope="openid offline_access")
    r = await client.post("/oauth/revoke", data={"client_id": "app1", "token": t["refresh_token"]})
    assert r.status_code == 200
    rf = await client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "app1",
                                                 "refresh_token": t["refresh_token"]})
    assert rf.json()["error"] == "invalid_grant"


# ── RP-initiated logout ────────────────────────────────────────────────────────

async def _logged_in_public(client, db, **kw):
    await make_client(db, post_logout=(POST_LOGOUT,), **kw)
    await make_account(db, "nguoi_dung")
    return await full_login(client, db, "nguoi_dung", scope="openid offline_access")


async def test_logout_with_hint_redirects_only_to_registered_uri_and_echoes_state(client, db):
    t = await _logged_in_public(client, db)
    r = await client.get("/oauth/logout", params={"id_token_hint": t["id_token"],
                                                  "post_logout_redirect_uri": POST_LOGOUT, "state": "s-9"})
    assert r.status_code == 302
    assert r.headers["location"] == f"{POST_LOGOUT}?state=s-9"
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 401
    # SSO cookie cũng chết: authorize lại phải đăng nhập
    again = await client.get("/oauth/authorize", params=authz_params(challenge=pkce()[1]))
    assert again.headers["location"].startswith("/oauth/login")


@pytest.mark.parametrize("evil", ["https://evil.example/", "https://app.example/bye/", "https://app.example/bye?x=1",
                                  "https://APP.example/bye", "//evil.example", "javascript:alert(1)"])
async def test_logout_open_redirect_is_blocked_but_session_still_ends(client, db, evil):
    t = await _logged_in_public(client, db)
    r = await client.get("/oauth/logout", params={"id_token_hint": t["id_token"], "post_logout_redirect_uri": evil})
    assert r.status_code == 200 and "location" not in r.headers
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 401


async def test_logout_rejects_invalid_or_foreign_hint(client, db):
    t = await _logged_in_public(client, db)
    for hint in ("rac", t["access_token"], t["id_token"][:-4] + "AAAA"):
        r = await client.get("/oauth/logout", params={"id_token_hint": hint})
        assert r.status_code == 400 and "location" not in r.headers
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 200   # chưa bị đăng xuất bởi hint sai
    r = await client.get("/oauth/logout", params={"id_token_hint": t["id_token"], "client_id": "app-khac"})
    assert r.status_code == 400


async def test_logout_accepts_expired_id_token_hint(client, db):
    t = await _logged_in_public(client, db)
    claims = jwt.decode(t["id_token"], options={"verify_signature": False})
    claims["exp"] = int(time.time()) - 3600
    old = await client.app.state.container.tokens.sign(claims, typ="JWT")
    r = await client.get("/oauth/logout", params={"id_token_hint": old, "post_logout_redirect_uri": POST_LOGOUT})
    assert r.status_code == 302
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 401


async def test_logout_without_hint_needs_csrf_confirmation(client, db):
    t = await _logged_in_public(client, db)
    page = await client.get("/oauth/logout", params={"client_id": "app1", "post_logout_redirect_uri": POST_LOGOUT})
    assert page.status_code == 200 and "<form" in page.text
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 200   # GET không tự đăng xuất (CSRF)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    bad = await client.post("/oauth/logout", data={"csrf": "y" * 40, "client_id": "app1"})
    assert bad.status_code == 403
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 200
    ok = await client.post("/oauth/logout", data={"csrf": csrf, "client_id": "app1",
                                                  "post_logout_redirect_uri": POST_LOGOUT})
    assert ok.status_code == 302 and ok.headers["location"] == POST_LOGOUT
    assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 401


async def test_logout_ends_every_client_session_under_the_same_sso(client, db):
    await make_client(db, "app1")
    await make_client(db, "app2", audience="app2-api")
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    toks = []
    for cid in ("app1", "app2"):
        v, ch = pkce()
        code = await get_code(client, authz_params(cid, challenge=ch, scope="openid offline_access"))
        toks.append((await exchange(client, code=code, verifier=v, client_id=cid)).json())
    r = await client.get("/oauth/logout", params={"id_token_hint": toks[0]["id_token"]})
    assert r.status_code == 200
    for t in toks:
        assert (await client.get("/oauth/userinfo", headers=bearer(t))).status_code == 401
        assert (await client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "x",
                                                        "refresh_token": t["refresh_token"]})).status_code == 401
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM sessions WHERE revoked_at IS NULL") == 0
        assert await conn.fetchval("SELECT count(*) FROM outbox WHERE type='session.revoked'") >= 2
