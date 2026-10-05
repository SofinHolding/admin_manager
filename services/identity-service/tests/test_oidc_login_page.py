"""Trang đăng nhập hosted: CSRF, cookie flags, clickjacking, rate limit, không enumeration, XSS; secret/log hygiene."""

from __future__ import annotations

import logging
import re
import statistics
import time
from dataclasses import replace
from urllib.parse import urlencode

import pytest
from conftest import _make_client
from oidc_util import (PASSWORD, REDIRECT, authz_params, basic_header, exchange, get_code, make_account, make_client,
                       pkce, query_of, sso_login)

from identity_service.security import hash_token

pytestmark = pytest.mark.needs_pg


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


async def _page(client, authz=""):
    return await client.get("/oauth/login", params={"authz": authz} if authz else None)


def _authz(client_id="app1", **kw) -> str:
    return urlencode(authz_params(client_id, challenge=pkce()[1], **kw))


# ── header chống clickjacking ──────────────────────────────────────────────────

async def test_login_error_consent_and_logout_pages_forbid_framing_and_caching(client, db):
    await make_client(db)
    await make_client(db, "third", first_party=False)
    await make_account(db, "nguoi_dung")
    pages = [await _page(client),
             await client.get("/oauth/authorize", params={"client_id": "khong-co"}),          # trang lỗi
             await client.get("/oauth/logout", params={"client_id": "app1"})]                 # xác nhận đăng xuất
    await sso_login(client, "nguoi_dung")
    pages.append(await client.get("/oauth/authorize", params=authz_params("third", challenge=pkce()[1])))  # consent
    for r in pages:
        assert r.status_code in (200, 400), r.status_code
        assert r.headers["x-frame-options"] == "DENY"
        csp = r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp and "script-src" not in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "no-store" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "<script" not in r.text.lower()


async def test_redirects_from_idp_also_carry_security_headers(client, db):
    await make_client(db)
    r = await client.get("/oauth/authorize", params=authz_params(challenge=pkce()[1]))
    assert r.status_code == 302 and r.headers["x-frame-options"] == "DENY" and "no-store" in r.headers["cache-control"]


# ── CSRF (double-submit) ───────────────────────────────────────────────────────

async def test_login_post_requires_matching_csrf_cookie_and_field(client, db):
    await make_client(db)
    await make_account(db, "nguoi_dung")
    page = await _page(client)
    good = _csrf(page.text)
    body = {"authz": "", "username": "nguoi_dung", "password": PASSWORD}
    # thiếu field
    assert (await client.post("/oauth/login", data=body)).status_code == 403
    # sai giá trị
    assert (await client.post("/oauth/login", data={**body, "csrf": "a" * 43})).status_code == 403
    # field đúng nhưng KHÔNG có cookie (kẻ tấn công cross-site không đặt được cookie)
    client.cookies.clear()
    assert (await client.post("/oauth/login", data={**body, "csrf": good})).status_code == 403
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM sso_sessions") == 0     # không phiên nào được tạo
    page = await _page(client)
    ok = await client.post("/oauth/login", data={**body, "csrf": _csrf(page.text)})
    assert ok.status_code == 303


async def test_csrf_token_is_random_per_render_and_bound_to_cookie(client, db):
    a, b = await _page(client), await _page(client)
    assert _csrf(a.text) != _csrf(b.text) and len(_csrf(a.text)) >= 40
    cookie = [v for k, v in client.cookies.items() if k == "idp_csrf"][0]
    assert cookie == _csrf(b.text)


# ── cookie flags ───────────────────────────────────────────────────────────────

async def test_cookie_flags_http_issuer(client, db):
    await make_account(db, "nguoi_dung")
    page = await _page(client)
    csrf_cookie = page.headers["set-cookie"]
    assert "idp_csrf=" in csrf_cookie and "HttpOnly" in csrf_cookie and "SameSite=strict" in csrf_cookie
    assert "Secure" not in csrf_cookie                    # issuer http (dev): không đặt Secure, không dùng __Host-
    r = await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": "", "username": "nguoi_dung",
                                                "password": PASSWORD})
    sso = r.headers["set-cookie"]
    assert sso.startswith("idp_sso=") and "HttpOnly" in sso and "SameSite=lax" in sso and "Path=/" in sso
    assert "Max-Age=28800" in sso


async def test_cookie_flags_https_issuer_use_host_prefix_and_secure(db, settings, mailer, clock, sleeps):
    https = replace(settings, issuer="https://identity.test")
    async with await _make_client(db, https, mailer, clock, sleeps) as client:
        await make_account(db, "nguoi_dung")
        client.base_url = "https://identity.test"
        page = await client.get("/oauth/login")
        c = page.headers["set-cookie"]
        assert c.startswith("__Host-idp_csrf=") and "Secure" in c and "HttpOnly" in c and "Path=/" in c
        assert "Domain" not in c
        r = await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": "", "username": "nguoi_dung",
                                                    "password": PASSWORD})
        sso = r.headers["set-cookie"]
        assert sso.startswith("__Host-idp_sso=") and "Secure" in sso and "HttpOnly" in sso and "SameSite=lax" in sso


async def test_sso_cookie_value_is_not_stored_in_clear(client, db):
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    raw = client.cookies["idp_sso"]
    async with db.connection() as conn:
        rows = await conn.fetch("SELECT token_hash FROM sso_sessions")
    assert [r["token_hash"] for r in rows] == [hash_token(raw)] and raw not in rows[0]["token_hash"]


async def test_new_login_rotates_sso_cookie_and_revokes_the_old_session(client, db):
    """Chống session fixation: mỗi lần đăng nhập đổi token; phiên SSO cũ của cookie hiện tại bị thu hồi."""
    from identity_service.accounts import repository as accounts

    aid = await make_account(db, "nguoi_dung")
    async with db.connection() as conn:
        account = await accounts.get_by_id(conn, aid)
    svc = client.app.state.container.oidc
    first, _ = await svc.sso_login(account, None)
    assert await svc.sso_lookup(first) is not None
    second, _ = await svc.sso_login(account, first)
    assert second != first
    assert await svc.sso_lookup(first) is None and await svc.sso_lookup(second) is not None


# ── enumeration, rate limit, trạng thái tài khoản ─────────────────────────────

async def test_failed_login_does_not_reveal_whether_username_exists(client, db, sleeps):
    await make_account(db, "nguoi_dung")
    texts, statuses = [], []
    for user, pw in (("nguoi_dung", "sai-mat-khau-1"), ("khong_ton_tai", "sai-mat-khau-1"),
                     ("nguoi_dung", ""), ("", "x")):
        page = await _page(client)
        r = await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": "", "username": user,
                                                    "password": pw})
        statuses.append(r.status_code)
        texts.append(re.search(r'role="alert">([^<]+)<', r.text).group(1))
    assert set(statuses) == {401} and len(set(texts)) == 1
    assert sleeps.count(0.0) >= 2          # đường sai mật khẩu đi qua cùng bước "delay" của login thường


async def test_login_timing_does_not_distinguish_unknown_user(client, db):
    await make_account(db, "nguoi_dung")

    async def attempt(user):
        page = await _page(client)
        start = time.perf_counter()
        await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": "", "username": user,
                                                "password": "sai-mat-khau-1"})
        return time.perf_counter() - start

    known = statistics.median([await attempt("nguoi_dung") for _ in range(3)])
    unknown = statistics.median([await attempt("khong_ton_tai") for _ in range(3)])
    assert known > 0.05 and unknown > 0.05            # cả hai đều tốn một lần bcrypt
    assert unknown > known * 0.5                       # tài khoản không tồn tại KHÔNG nhanh hơn rõ rệt


async def test_login_is_rate_limited_per_ip(client, db):
    await make_account(db, "nguoi_dung")
    codes = []
    for _ in range(12):
        page = await _page(client)
        r = await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": "", "username": "x_y_z",
                                                    "password": "sai-sai-sai"})
        codes.append(r.status_code)
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    assert int(r.headers["retry-after"]) > 0
    page = await _page(client)                         # ngay cả mật khẩu đúng cũng bị chặn khi đã vượt hạn mức
    ok = await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": "", "username": "nguoi_dung",
                                                 "password": PASSWORD})
    assert ok.status_code == 429


async def test_pending_and_suspended_accounts_cannot_start_sso_session(client, db):
    await make_account(db, "cho_xac_thuc", status="pending", verified=False)
    await make_account(db, "bi_khoa", status="suspended")
    for user in ("cho_xac_thuc", "bi_khoa"):
        r = await sso_login(client, user)
        assert r.status_code == 403 and "set-cookie" not in r.headers or "idp_sso" not in r.headers.get("set-cookie", "")
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM sso_sessions") == 0
    r = await sso_login(client, "bi_khoa", password="sai-mat-khau-1")        # sai mật khẩu: vẫn chỉ là 401 chung
    assert r.status_code == 401


# ── XSS / open redirect qua trang login ────────────────────────────────────────

async def test_login_page_escapes_reflected_authorize_parameters(client, db):
    await make_client(db)
    xss = '"><img src=x onerror=alert(1)><script>alert(2)</script>'
    # `authz` thô do kẻ tấn công kiểm soát (không qua urlencode): phần thừa chứa ký tự HTML nguy hiểm
    authz = _authz(state=xss, nonce=xss) + "&junk=" + xss
    r = await _page(client, authz)
    assert r.status_code == 200
    assert "<script" not in r.text and "<img" not in r.text and "onerror=alert(1)>" not in r.text
    assert "&lt;script&gt;" in r.text and "&quot;&gt;&lt;img" in r.text               # đã bị escape thành văn bản
    # tên client hiển thị cũng bị escape
    async with db.connection() as conn:
        await conn.execute("UPDATE oidc_clients SET name = $1", "<b>Ten</b>")
    r = await _page(client, _authz())
    assert "<b>Ten</b>" not in r.text and "&lt;b&gt;Ten&lt;/b&gt;" in r.text


async def test_login_rejects_authz_with_untrusted_redirect_uri_instead_of_redirecting(client, db):
    await make_client(db)
    await make_account(db, "nguoi_dung")
    evil = _authz(redirect_uri="https://evil.example/cb")
    r = await _page(client, evil)
    assert r.status_code == 400 and "location" not in r.headers
    page = await _page(client)
    r = await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": evil, "username": "nguoi_dung",
                                                "password": PASSWORD})
    assert r.status_code == 400 and "location" not in r.headers
    r = await _page(client, "x" * 5000)
    assert r.status_code == 400


async def test_successful_login_continues_the_authorize_flow(client, db):
    await make_client(db)
    await make_account(db, "nguoi_dung")
    ch = pkce()[1]
    params = authz_params(challenge=ch)
    first = await client.get("/oauth/authorize", params=params)
    login_url = first.headers["location"]
    page = await client.get(login_url)
    authz = re.search(r'name="authz" value="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    r = await client.post("/oauth/login", data={"csrf": _csrf(page.text), "authz": authz, "username": "nguoi_dung",
                                                "password": PASSWORD})
    assert r.status_code == 303 and r.headers["location"] == "/oauth/authorize?" + authz
    final = await client.get(r.headers["location"])
    assert final.status_code == 302 and query_of(final.headers["location"])["state"] == "st-123"
    assert "code" in query_of(final.headers["location"])


# ── bí mật không bị lưu rõ / không vào log ─────────────────────────────────────

async def test_client_secret_is_stored_only_as_hash(client, db, mailer):
    from conftest import token_for
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    r = await client.post("/v1/admin/oidc/clients", headers=admin["auth"],
                          json={"name": "Backend", "type": "confidential", "redirect_uris": [REDIRECT]})
    secret = r.json()["client_secret"]
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM oidc_clients WHERE client_id=$1", r.json()["client_id"])
        dump = " ".join(str(v) for v in dict(row).values())
    assert secret not in dump and row["secret_hash"] == hash_token(secret)
    listing = await client.get("/v1/admin/oidc/clients", headers=admin["auth"])
    assert secret not in listing.text and "secret" not in listing.text.replace("client_secret_", "")
    assert "client_secret" not in (await client.get(f"/v1/admin/oidc/clients/{r.json()['client_id']}",
                                                    headers=admin["auth"])).text


async def test_no_code_token_secret_or_password_reaches_service_logs(client, db, caplog):
    caplog.set_level(logging.DEBUG)
    secret = await make_client(db, "conf-app", ctype="confidential", audience="conf-api")
    await make_account(db, "nguoi_dung")
    await sso_login(client, "nguoi_dung")
    sso_cookie = client.cookies["idp_sso"]
    code = await get_code(client, authz_params("conf-app", scope="openid offline_access"))
    r = await exchange(client, code=code, verifier=None, client_id="conf-app", secret=secret)
    tokens = r.json()
    await exchange(client, code=code, verifier=None, client_id="conf-app", secret=secret)           # tái dùng code
    await client.post("/oauth/token", headers=basic_header("conf-app", "sai"), data={"grant_type": "refresh_token",
                                                                                      "refresh_token": "x"})
    await client.post("/oauth/introspect", headers=basic_header("conf-app", secret),
                      data={"token": tokens["access_token"]})
    await client.post("/oauth/revoke", headers=basic_header("conf-app", secret), data={"token": tokens["refresh_token"]})
    await client.get("/oauth/logout", params={"id_token_hint": tokens["id_token"]})
    log = " | ".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records
                     if r.name.startswith(("identity", "uvicorn", "manager")))
    for value in (code, secret, tokens["access_token"], tokens["refresh_token"], tokens["id_token"], PASSWORD,
                  sso_cookie):
        assert value not in log
