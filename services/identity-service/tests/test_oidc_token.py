"""POST /oauth/token: PKCE, auth code một lần, TTL, redirect_uri, xác thực client, id_token, refresh xoay vòng."""

from __future__ import annotations

import base64
import hashlib

import jwt
import pytest
from oidc_util import (REDIRECT, authz_params, basic_header, exchange, full_login, get_code, jwks_key, make_account,
                       make_client, pkce, sso_login)

pytestmark = pytest.mark.needs_pg

NO_STORE_HEADERS = ("no-store", "no-cache")


async def _setup(client, db, **client_kw):
    secret = await make_client(db, **client_kw)
    await make_account(db, "nguoi_dung")
    return secret


# ── PKCE ────────────────────────────────────────────────────────────────────────

async def test_pkce_correct_verifier_succeeds_and_response_is_not_cacheable(client, db):
    await _setup(client, db)
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch))
    r = await exchange(client, code=code, verifier=v)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 900 and "refresh_token" not in body


async def test_pkce_wrong_missing_or_malformed_verifier_is_invalid_grant_and_burns_code(client, db):
    await _setup(client, db)
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    for bad in (pkce()[0], None, "ngan", "a" * 200):
        code = await get_code(client, authz_params(challenge=ch))
        r = await exchange(client, code=code, verifier=bad)
        assert (r.status_code, r.json()["error"]) == (400, "invalid_grant"), bad
        again = await exchange(client, code=code, verifier=v)   # code đã bị đốt: kể cả verifier đúng cũng không dùng được
        assert again.json()["error"] == "invalid_grant"


async def test_pkce_downgrade_verifier_without_challenge_is_rejected_for_confidential(client, db):
    secret = await _setup(client, db, ctype="confidential")
    await sso_login(client, "nguoi_dung")
    code = await get_code(client, authz_params())  # confidential: PKCE tuỳ chọn ở authorize
    r = await exchange(client, code=code, verifier=pkce()[0], secret=secret)
    assert r.json()["error"] == "invalid_grant"
    code = await get_code(client, authz_params())
    ok = await exchange(client, code=code, verifier=None, secret=secret)
    assert ok.status_code == 200


async def test_pkce_challenge_bound_at_authorize_cannot_be_dropped_by_confidential_client(client, db):
    secret = await _setup(client, db, ctype="confidential")
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch))
    assert (await exchange(client, code=code, verifier=None, secret=secret)).json()["error"] == "invalid_grant"
    code = await get_code(client, authz_params(challenge=ch))
    assert (await exchange(client, code=code, verifier=v, secret=secret)).status_code == 200


# ── auth code: một lần, TTL, ràng buộc ─────────────────────────────────────────

async def test_code_reuse_is_invalid_grant_and_revokes_everything_issued_from_it(client, db):
    await _setup(client, db)
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch, scope="openid offline_access"))
    first = await exchange(client, code=code, verifier=v)
    assert first.status_code == 200
    tokens = first.json()
    assert (await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"})).status_code == 200
    replay = await exchange(client, code=code, verifier=v)
    assert (replay.status_code, replay.json()["error"]) == (400, "invalid_grant")
    # token đã cấp từ code đó bị thu hồi: access token chết ngay, refresh token chết
    assert (await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"})).status_code == 401
    r = await client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "app1",
                                                "refresh_token": tokens["refresh_token"]})
    assert r.json()["error"] == "invalid_grant"
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM oidc_refresh_tokens WHERE revoked_at IS NULL") == 0
        assert await conn.fetchval("SELECT count(*) FROM outbox WHERE type='session.revoked'") >= 1


async def test_code_expires_after_ttl(client, db, clock):
    await _setup(client, db)
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch))
    clock.advance(seconds=61)
    r = await exchange(client, code=code, verifier=v)
    assert (r.status_code, r.json()["error"]) == (400, "invalid_grant")


async def test_code_still_valid_just_before_ttl(client, db, clock):
    await _setup(client, db)
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch))
    clock.advance(seconds=59)
    assert (await exchange(client, code=code, verifier=v)).status_code == 200


async def test_token_endpoint_redirect_uri_must_match_authorize_request(client, db):
    await make_client(db, redirect_uris=(REDIRECT, "https://app.example/other"))
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    for bad in ("https://app.example/other", "https://app.example/cb/", "https://evil.example/cb"):
        code = await get_code(client, authz_params(challenge=ch))
        r = await exchange(client, code=code, verifier=v, redirect_uri=bad)
        assert r.json()["error"] == "invalid_grant", bad
    code = await get_code(client, authz_params(challenge=ch))
    assert (await exchange(client, code=code, verifier=v, redirect_uri=REDIRECT)).status_code == 200


async def test_code_issued_to_other_client_is_rejected(client, db):
    await make_client(db, "app1")
    await make_client(db, "app2", redirect_uris=(REDIRECT,))
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params("app1", challenge=ch))
    r = await exchange(client, code=code, verifier=v, client_id="app2")
    assert r.json()["error"] == "invalid_grant"


async def test_unknown_code_and_missing_parameters(client, db):
    await _setup(client, db)
    assert (await exchange(client, code="khong-co-code", verifier=pkce()[0])).json()["error"] == "invalid_grant"
    r = await client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": "app1",
                                                "redirect_uri": REDIRECT})
    assert r.json()["error"] == "invalid_request"
    r = await client.post("/oauth/token", data={"client_id": "app1"})
    assert r.json()["error"] == "invalid_request"
    r = await client.post("/oauth/token", data={"grant_type": "password", "client_id": "app1"})
    assert r.json()["error"] == "unsupported_grant_type"


async def test_suspended_account_cannot_redeem_code(client, db):
    await _setup(client, db)
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch))
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended'")
    assert (await exchange(client, code=code, verifier=v)).json()["error"] == "invalid_grant"


# ── xác thực client ─────────────────────────────────────────────────────────────

async def test_confidential_client_authentication_methods(client, db):
    secret = await _setup(client, db, ctype="confidential")
    await sso_login(client, "nguoi_dung")
    for basic in (True, False):
        v, ch = pkce()
        code = await get_code(client, authz_params(challenge=ch))
        r = await exchange(client, code=code, verifier=v, secret=secret, basic=basic)
        assert r.status_code == 200, (basic, r.text)


async def test_wrong_or_missing_client_secret_is_invalid_client_401(client, db):
    secret = await _setup(client, db, ctype="confidential")
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch))
    for bad in (None, "sai-secret", secret + "x", ""):
        r = await exchange(client, code=code, verifier=v, secret=bad)
        assert (r.status_code, r.json()["error"]) == (401, "invalid_client"), bad
    r = await exchange(client, code=code, verifier=v, secret="sai", basic=True)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    # code vẫn còn dùng được vì xác thực client thất bại TRƯỚC khi chạm vào code
    assert (await exchange(client, code=code, verifier=v, secret=secret)).status_code == 200


async def test_unknown_client_is_invalid_client(client, db):
    await _setup(client, db)
    r = await exchange(client, code="x", verifier=pkce()[0], client_id="khong-co")
    assert (r.status_code, r.json()["error"]) == (401, "invalid_client")


async def test_public_client_must_not_present_a_secret(client, db):
    await _setup(client, db)
    await sso_login(client, "nguoi_dung")
    v, ch = pkce()
    code = await get_code(client, authz_params(challenge=ch))
    r = await exchange(client, code=code, verifier=v, secret="mot-secret-bat-ky")
    assert (r.status_code, r.json()["error"]) == (401, "invalid_client")


async def test_two_client_auth_methods_at_once_is_invalid_request(client, db):
    secret = await _setup(client, db, ctype="confidential")
    r = await client.post("/oauth/token", headers=basic_header("app1", secret),
                          data={"grant_type": "authorization_code", "client_secret": secret, "code": "x",
                                "redirect_uri": REDIRECT})
    assert r.json()["error"] == "invalid_request"


async def test_duplicate_form_parameters_and_wrong_content_type_rejected(client, db):
    await _setup(client, db)
    r = await client.post("/oauth/token", content="grant_type=authorization_code&grant_type=refresh_token&client_id=app1",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.json()["error"] == "invalid_request"
    r = await client.post("/oauth/token", json={"grant_type": "authorization_code"})
    assert (r.status_code, r.json()["error"]) == (400, "invalid_request")


# ── id_token / access_token ────────────────────────────────────────────────────

async def test_id_token_verifies_via_jwks_with_all_required_claims(client, db):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung", nonce="n-0S6_WzA2Mj")
    idt = t["id_token"]
    header = jwt.get_unverified_header(idt)
    assert header["alg"] == "RS256" and header["kid"]
    key = await jwks_key(client, idt)
    claims = jwt.decode(idt, key, algorithms=["RS256"], audience="app1", issuer="http://identity.test")
    assert claims["nonce"] == "n-0S6_WzA2Mj"
    assert claims["aud"] == "app1" and claims["azp"] == "app1"
    assert claims["exp"] > claims["iat"] and claims["auth_time"] <= claims["iat"] + 1
    expected = base64.urlsafe_b64encode(hashlib.sha256(t["access_token"].encode()).digest()[:16]).rstrip(b"=").decode()
    assert claims["at_hash"] == expected
    at = jwt.decode(t["access_token"], key, algorithms=["RS256"], audience="manager-api", issuer="http://identity.test")
    assert claims["sid"] == at["sid"] and claims["sub"] == at["sub"]
    assert claims["preferred_username"] == "nguoi_dung" and claims["email"] == "nguoi_dung@example.com"
    assert set(at["aud"]) == {"manager-api", "app1-api"} and at["client_id"] == "app1"
    assert at["scope"] == "openid profile email" and at["role"] == "viewer"


async def test_id_token_is_rejected_when_verified_with_wrong_audience_or_as_access_token(client, db):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung")
    key = await jwks_key(client, t["id_token"])
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(t["id_token"], key, algorithms=["RS256"], audience="app-khac", issuer="http://identity.test")
    with pytest.raises(jwt.InvalidIssuerError):
        jwt.decode(t["id_token"], key, algorithms=["RS256"], audience="app1", issuer="http://khac")
    # id_token cùng khoá ký nhưng KHÔNG dùng được như access token (typ + aud khác)
    r = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {t['id_token']}"})
    assert r.status_code == 401
    r = await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {t['id_token']}"})
    assert r.status_code == 401


async def test_nonce_is_omitted_when_not_sent_and_scope_limits_claims(client, db):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung", scope="openid", nonce=None)
    key = await jwks_key(client, t["id_token"])
    claims = jwt.decode(t["id_token"], key, algorithms=["RS256"], audience="app1")
    assert "nonce" not in claims and "email" not in claims and "preferred_username" not in claims


async def test_access_token_is_also_valid_for_manager_api_and_carries_perms(client, db):
    await _setup(client, db)
    async with db.connection() as conn:
        await conn.execute("INSERT INTO account_permissions (account_id, permission) "
                           "SELECT id, 'reward:access' FROM accounts")
    t = await full_login(client, db, "nguoi_dung")
    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {t['access_token']}"})
    assert me.status_code == 200 and me.json()["username"] == "nguoi_dung"
    key = await jwks_key(client, t["access_token"])
    at = jwt.decode(t["access_token"], key, algorithms=["RS256"], audience="manager-api")
    assert at["perms"] == ["reward:access"]


# ── refresh token xoay vòng ────────────────────────────────────────────────────

async def _refresh(client, rt, client_id="app1", secret=None, **extra):
    data = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": rt, **extra}
    if secret:
        data["client_secret"] = secret
    return await client.post("/oauth/token", data=data)


async def test_refresh_token_only_issued_with_offline_access(client, db):
    await _setup(client, db)
    assert "refresh_token" not in await full_login(client, db, "nguoi_dung", scope="openid")


async def test_refresh_rotation_issues_new_token_and_invalidates_old(client, db):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung", scope="openid offline_access")
    r1 = await _refresh(client, t["refresh_token"])
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["refresh_token"] != t["refresh_token"] and b1["access_token"] and "id_token" not in b1
    r2 = await _refresh(client, b1["refresh_token"])
    assert r2.status_code == 200 and r2.json()["refresh_token"] not in (t["refresh_token"], b1["refresh_token"])
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(DISTINCT family_id) FROM oidc_refresh_tokens") == 1
        assert await conn.fetchval("SELECT count(*) FROM oidc_refresh_tokens WHERE used_at IS NULL") == 1


async def test_reusing_a_rotated_refresh_token_revokes_the_whole_family_and_session(client, db):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung", scope="openid offline_access")
    r1 = (await _refresh(client, t["refresh_token"])).json()
    r2 = (await _refresh(client, r1["refresh_token"])).json()
    stolen = await _refresh(client, t["refresh_token"])            # kẻ trộm dùng lại token ĐÃ xoay
    assert (stolen.status_code, stolen.json()["error"]) == (400, "invalid_grant")
    assert (await _refresh(client, r2["refresh_token"])).json()["error"] == "invalid_grant"   # token hợp lệ mới nhất cũng chết
    bearer = {"Authorization": f"Bearer {r2['access_token']}"}
    assert (await client.get("/oauth/userinfo", headers=bearer)).status_code == 401           # phiên bị thu hồi
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM oidc_refresh_tokens WHERE revoked_at IS NULL") == 0
        assert await conn.fetchval("SELECT count(*) FROM sessions WHERE client_type='oidc' AND revoked_at IS NULL") == 0


async def test_refresh_requires_the_same_client_and_revokes_on_mismatch(client, db):
    await make_client(db, "app1")
    await make_client(db, "app2")
    await make_account(db, "nguoi_dung")
    t = await full_login(client, db, "nguoi_dung", scope="openid offline_access")
    assert (await _refresh(client, t["refresh_token"], client_id="app2")).json()["error"] == "invalid_grant"
    assert (await _refresh(client, t["refresh_token"])).json()["error"] == "invalid_grant"   # family đã bị thu hồi


async def test_refresh_cannot_widen_scope_but_can_narrow(client, db):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung", scope="openid profile offline_access")
    assert (await _refresh(client, t["refresh_token"], scope="openid email")).json()["error"] == "invalid_scope"
    r = await _refresh(client, t["refresh_token"], scope="openid")
    assert r.status_code == 200 and r.json()["scope"] == "openid"


async def test_refresh_fails_when_account_suspended_or_session_revoked_or_expired(client, db, clock):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung", scope="openid offline_access")
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended'")
    assert (await _refresh(client, t["refresh_token"])).json()["error"] == "invalid_grant"
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='active'")
    t2 = await full_login(client, db, "nguoi_dung", scope="openid offline_access")
    clock.advance(days=31)
    assert (await _refresh(client, t2["refresh_token"])).json()["error"] == "invalid_grant"


async def test_refresh_token_stored_hashed_and_unknown_token_is_invalid_grant(client, db):
    await _setup(client, db)
    t = await full_login(client, db, "nguoi_dung", scope="openid offline_access")
    async with db.connection() as conn:
        hashes = [r["token_hash"] for r in await conn.fetch("SELECT token_hash FROM oidc_refresh_tokens")]
    assert t["refresh_token"] not in hashes and len(hashes) == 1
    assert (await _refresh(client, "khong-ton-tai")).json()["error"] == "invalid_grant"
    assert (await client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "app1"})
            ).json()["error"] == "invalid_request"


async def test_confidential_client_refresh_requires_secret(client, db):
    secret = await _setup(client, db, ctype="confidential")
    await sso_login(client, "nguoi_dung")
    code = await get_code(client, authz_params(scope="openid offline_access"))
    t = (await exchange(client, code=code, verifier=None, secret=secret)).json()
    assert (await _refresh(client, t["refresh_token"])).status_code == 401
    assert (await _refresh(client, t["refresh_token"], secret="sai")).status_code == 401
    assert (await _refresh(client, t["refresh_token"], secret=secret)).status_code == 200


async def test_only_typ_at_jwt_is_accepted_as_access_token_even_with_right_audience(client, db):
    """Chặn nhầm lẫn loại token: JWT cùng khoá ký, aud/iss đúng nhưng `typ` không phải at+jwt thì KHÔNG phải access token."""
    await _setup(client, db)
    async with db.connection() as conn:
        sub = await conn.fetchval("SELECT id FROM accounts")
    now = int(__import__("time").time())
    claims = {"iss": "http://identity.test", "aud": ["manager-api"], "sub": sub, "username": "nguoi_dung",
              "role": "viewer", "perms": [], "iat": now, "nbf": now, "exp": now + 300}
    tokens = client.app.state.container.tokens
    as_jwt = await tokens.sign(claims, typ="JWT")
    as_logout = await tokens.sign(claims, typ="logout+jwt")
    as_access = await tokens.sign(claims, typ="at+jwt")
    for bad in (as_jwt, as_logout):
        assert (await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})).status_code == 401
    assert (await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {as_access}"})).status_code == 200
