"""OIDC discovery và /oauth/authorize: mọi nhánh lỗi, so khớp redirect_uri chính xác, state, nonce, PKCE ở bước authorize."""

from __future__ import annotations

import pytest
from oidc_util import (REDIRECT, authz_params, get_code, make_account, make_client, pkce, query_of, sso_login)

pytestmark = pytest.mark.needs_pg


# ── discovery ───────────────────────────────────────────────────────────────────

async def test_discovery_document_is_complete_and_consistent_with_jwks(client):
    r = await client.get("/.well-known/openid-configuration")
    assert r.status_code == 200
    d = r.json()
    assert d["issuer"] == "http://identity.test"
    assert d["response_types_supported"] == ["code"]
    assert d["code_challenge_methods_supported"] == ["S256"]
    assert d["id_token_signing_alg_values_supported"] == ["RS256"]
    assert set(d["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert d["backchannel_logout_supported"] is True and d["backchannel_logout_session_supported"] is True
    assert "none" in d["token_endpoint_auth_methods_supported"]
    assert "client_secret_basic" in d["introspection_endpoint_auth_methods_supported"]
    for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri", "introspection_endpoint",
                "revocation_endpoint", "end_session_endpoint"):
        assert d[key].startswith("http://identity.test/")
    assert (await client.get("/.well-known/jwks.json")).json()["keys"]  # jwks_uri trỏ tới tài liệu có thật


# ── client_id / redirect_uri sai: KHÔNG redirect ────────────────────────────────

async def _is_error_page(r):
    assert r.status_code == 400
    assert "location" not in r.headers
    assert r.headers["content-type"].startswith("text/html")


async def test_unknown_client_shows_error_page_without_redirect(client, db):
    await make_client(db)
    await _is_error_page(await client.get("/oauth/authorize", params=authz_params("khong-co", challenge=pkce()[1])))


async def test_disabled_client_is_rejected_without_redirect(client, db):
    await make_client(db, status="disabled")
    await _is_error_page(await client.get("/oauth/authorize", params=authz_params(challenge=pkce()[1])))


@pytest.mark.parametrize("bad", [
    "https://app.example/cb/",              # thêm dấu / cuối
    "https://app.example/cb?x=1",           # thêm query
    "https://APP.example/cb",               # đổi hoa-thường của host
    "https://app.example/CB",               # đổi hoa-thường của path
    "https://app.example/cb#frag",          # fragment
    "https://app.example/cb/../cb",         # path traversal
    "https://app.example@evil.example/cb",  # userinfo: host thật là evil.example
    "https://app.example.evil.example/cb",  # tiền tố host
    "https://app.example/cbx",              # prefix của path
    "https://app.example/c",                # substring
    "http://app.example/cb",                # hạ cấp scheme
    "https://app.example:443/cb",           # cổng mặc định tường minh
    "https://app.example/cb ",              # khoảng trắng cuối
    " https://app.example/cb",
    "https://app.example/%63b",             # percent-encode
    "javascript:alert(1)",
    "",
])
async def test_redirect_uri_must_match_exactly_else_page_not_redirect(client, db, bad):
    await make_client(db)
    r = await client.get("/oauth/authorize", params=authz_params(challenge=pkce()[1], redirect_uri=bad))
    await _is_error_page(r)


async def test_missing_redirect_uri_or_client_id_is_page_error(client, db):
    await make_client(db)
    p = authz_params(challenge=pkce()[1])
    for drop in ("redirect_uri", "client_id"):
        q = {k: v for k, v in p.items() if k != drop}
        await _is_error_page(await client.get("/oauth/authorize", params=q))


async def test_duplicate_redirect_uri_parameter_is_page_error(client, db):
    await make_client(db, redirect_uris=(REDIRECT, "https://evil.example/cb"))
    url = ("/oauth/authorize?response_type=code&client_id=app1&redirect_uri=https%3A%2F%2Fevil.example%2Fcb"
           f"&redirect_uri={REDIRECT}&state=s&code_challenge={pkce()[1]}&code_challenge_method=S256&scope=openid")
    await _is_error_page(await client.get(url))


async def test_error_page_escapes_html_and_has_no_reflection_of_input(client, db):
    await make_client(db)
    xss = '"><script>alert(1)</script>'
    r = await client.get("/oauth/authorize", params=authz_params(xss, challenge=pkce()[1], redirect_uri=xss, state=xss))
    await _is_error_page(r)
    assert "<script>" not in r.text and "alert(1)" not in r.text


# ── lỗi sau khi redirect_uri đã tin cậy: redirect về client kèm state ─────────

async def _redirect_error(client, params, expected_error):
    r = await client.get("/oauth/authorize", params=params)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith(REDIRECT + "?")
    q = query_of(loc)
    assert q["error"] == expected_error, q
    assert q["iss"] == "http://identity.test"
    return q


async def test_unsupported_response_type_redirects_with_state(client, db):
    await make_client(db)
    for rt in ("token", "id_token", "code token"):
        q = await _redirect_error(client, authz_params(challenge=pkce()[1], response_type=rt), "unsupported_response_type")
        assert q["state"] == "st-123"


async def test_missing_response_type_is_invalid_request(client, db):
    await make_client(db)
    p = authz_params(challenge=pkce()[1])
    del p["response_type"]
    await _redirect_error(client, p, "invalid_request")


async def test_state_is_required_and_echoed_exactly(client, db):
    await make_client(db)
    p = authz_params(challenge=pkce()[1])
    del p["state"]
    q = await _redirect_error(client, p, "invalid_request")
    assert "state" not in q
    odd = "a b&c=d/é?#x"
    q = await _redirect_error(client, authz_params(challenge=pkce()[1], state=odd, response_type="token"),
                              "unsupported_response_type")
    assert q["state"] == odd  # URL-encode đúng, không bị cắt/khung sai


async def test_pkce_is_mandatory_for_public_clients_and_plain_is_rejected(client, db):
    await make_client(db)
    await _redirect_error(client, authz_params(), "invalid_request")                       # thiếu challenge
    _, ch = pkce()
    await _redirect_error(client, authz_params(challenge=ch, code_challenge_method="plain"), "invalid_request")
    p = authz_params(challenge=ch)
    del p["code_challenge_method"]                                                          # mặc định "plain" bị cấm
    await _redirect_error(client, p, "invalid_request")
    await _redirect_error(client, authz_params(challenge="ngan", ), "invalid_request")      # challenge sai định dạng
    await _redirect_error(client, authz_params(code_challenge_method="S256"), "invalid_request")  # method không challenge


async def test_scope_must_be_openid_and_allowed(client, db):
    await make_client(db, scopes=("openid", "profile"))
    ch = pkce()[1]
    await _redirect_error(client, authz_params(challenge=ch, scope="profile"), "invalid_scope")        # thiếu openid
    await _redirect_error(client, authz_params(challenge=ch, scope="openid email"), "invalid_scope")   # ngoài allowed
    await _redirect_error(client, authz_params(challenge=ch, scope="openid openid"), "invalid_scope")  # lặp
    await _redirect_error(client, authz_params(challenge=ch, scope="openid admin"), "invalid_scope")
    await _redirect_error(client, authz_params(challenge=ch, scope="openid offline_access"), "invalid_scope")


async def test_request_object_parameters_are_not_supported(client, db):
    await make_client(db)
    ch = pkce()[1]
    await _redirect_error(client, authz_params(challenge=ch, request="eyJ"), "request_not_supported")
    await _redirect_error(client, authz_params(challenge=ch, request_uri="https://x/y"), "request_uri_not_supported")


async def test_duplicate_non_critical_parameter_is_invalid_request(client, db):
    await make_client(db)
    url = (f"/oauth/authorize?response_type=code&client_id=app1&redirect_uri={REDIRECT}&state=a&state=b"
           f"&scope=openid&code_challenge={pkce()[1]}&code_challenge_method=S256")
    r = await client.get(url)
    assert r.status_code == 302 and query_of(r.headers["location"])["error"] == "invalid_request"


async def test_prompt_none_without_session_returns_login_required(client, db):
    await make_client(db)
    await _redirect_error(client, authz_params(challenge=pkce()[1], prompt="none"), "login_required")


async def test_unauthenticated_user_is_sent_to_hosted_login_with_authorize_request(client, db):
    await make_client(db)
    p = authz_params(challenge=pkce()[1])
    r = await client.get("/oauth/authorize", params=p)
    assert r.status_code == 302 and r.headers["location"].startswith("/oauth/login?authz=")
    assert "code=" not in r.headers["location"]


# ── thành công ──────────────────────────────────────────────────────────────────

async def test_first_party_client_gets_code_without_consent(client, db):
    await make_client(db, first_party=True)
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    code = await get_code(client, authz_params(challenge=pkce()[1]))
    assert len(code) >= 40
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM oidc_auth_codes")
        assert row["code_hash"] != code and code not in str(dict(row).values())   # chỉ lưu hash
        assert (row["client_id"], row["redirect_uri"], row["nonce"], row["used_at"]) == ("app1", REDIRECT, "nonce-1", None)
        assert (row["expires_at"] - row["created_at"]).total_seconds() == 60


async def test_third_party_client_requires_consent_and_deny_returns_access_denied(client, db):
    await make_client(db, first_party=False)
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    p = authz_params(challenge=pkce()[1])
    page = await client.get("/oauth/authorize", params=p)
    assert page.status_code == 200 and "Cho phép" in page.text
    import re
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    authz = re.search(r'name="authz" value="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    deny = await client.post("/oauth/authorize", data={"csrf": csrf, "authz": authz, "decision": "deny"})
    assert deny.status_code == 302 and query_of(deny.headers["location"])["error"] == "access_denied"
    allow = await client.post("/oauth/authorize", data={"csrf": csrf, "authz": authz, "decision": "allow"})
    assert allow.status_code == 302 and "code" in query_of(allow.headers["location"])
    # Không có CSRF hợp lệ thì không cấp code
    bad = await client.post("/oauth/authorize", data={"csrf": "x" * 40, "authz": authz, "decision": "allow"})
    assert bad.status_code == 403 and "location" not in bad.headers


async def test_prompt_none_with_session_but_consent_needed_returns_consent_required(client, db):
    await make_client(db, first_party=False)
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    r = await client.get("/oauth/authorize", params=authz_params(challenge=pkce()[1], prompt="none"))
    assert query_of(r.headers["location"])["error"] == "consent_required"


async def test_suspended_account_sso_session_cannot_authorize(client, db):
    await make_client(db)
    aid = await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended' WHERE id=$1", aid)
    r = await client.get("/oauth/authorize", params=authz_params(challenge=pkce()[1]))
    assert r.headers["location"].startswith("/oauth/login")  # phiên SSO coi như không còn
