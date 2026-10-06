"""Hồi quy từ đợt review: mật khẩu >72 byte (bcrypt 5), dash_admin vs admin, audience token OIDC bên thứ ba,
đổi email phải xác thực lại, rate limiter dọn nhầm khóa.

Mỗi test khoá một lỗi đã xác nhận; xem docs/handbook/02-trang-thai-tinh-nang.md mục 2.3.
"""

from __future__ import annotations

import time

import bcrypt
import pytest
from manager_sdk import TokenVerifier
from manager_sdk.errors import AuthError
from oidc_util import (authz_params, basic_header, exchange, full_login, get_code, make_account, make_client,
                       pkce, sso_login)

from conftest import ISSUER, login, make_key, register, register_verified, token_for
from identity_service.auth.ratelimit import RateLimiter
from identity_service.security import hash_password, verify_password

PW_72 = "a" * 72
PW_73 = "a" * 73
PW_VN_73 = "ă" * 36 + "a"  # 36 ký tự 2 byte + 1 = 73 byte nhưng chỉ 37 ký tự


# ═══ 1. Mật khẩu >72 byte ═══════════════════════════════════════════════════════

def test_hash_and_verify_never_raise_on_long_passwords_and_truncate_like_old_bcrypt():
    long_pw = "x" * 80
    h = hash_password(long_pw)          # trước fix: ValueError (bcrypt 5)
    assert verify_password(long_pw, h)
    assert verify_password("x" * 72, h)  # bcrypt<5 cắt 72 byte: hành vi cũ được giữ


def test_legacy_hash_made_from_80_byte_password_still_verifies():
    """Hash do bcrypt<5 (cắt âm thầm 72 byte) — mô phỏng bằng hashpw(pw[:72])."""
    pw = ("mat-khau-rat-dai-" * 5)[:80]
    legacy = bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()
    assert verify_password(pw, legacy) is True
    assert verify_password("sai" + pw, legacy) is False


@pytest.mark.needs_pg
async def test_legacy_80_byte_hash_can_log_in(client, db, mailer):
    pw = ("mat-khau-rat-dai-" * 5)[:80]
    await register_verified(client, db, mailer, username="hash_cu_dai")
    legacy = bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET password_hash=$1 WHERE username='hash_cu_dai'", legacy)
    assert (await login(client, "hash_cu_dai", password=pw)).status_code == 200
    assert (await login(client, "hash_cu_dai", password="sai-mat-khau")).status_code == 401


@pytest.mark.needs_pg
async def test_register_accepts_72_bytes_and_rejects_73_with_422_without_echo(client, db):
    ok = await register(client, db, username="mk_72_byte", password=PW_72)
    assert ok.status_code == 200, ok.text
    for name, pw in (("mk_73_byte", PW_73), ("mk_73_tv", PW_VN_73)):
        r = await register(client, db, username=name, password=pw)
        assert r.status_code == 422, r.text
        assert "72 byte" in r.text
        assert pw not in r.text  # không phản chiếu giá trị mật khẩu


@pytest.mark.needs_pg
async def test_reset_and_change_password_reject_73_bytes(client, db, mailer):
    t = await token_for(client, db, mailer, username="doi_mk_dai")
    await client.post("/v1/auth/forgot-password", json={"email": "doi_mk_dai@example.com"})
    r = await client.post("/v1/auth/reset-password", json={"token": mailer.last("reset"), "new_password": PW_73})
    assert r.status_code == 422 and PW_73 not in r.text
    await client.post("/v1/auth/change-password/send-code", headers=t["auth"])
    r = await client.post("/v1/auth/change-password", headers=t["auth"],
                          json={"code": mailer.last("code"), "new_password": PW_73})
    assert r.status_code == 422 and PW_73 not in r.text
    # mật khẩu cũ vẫn đăng nhập được (không bị đổi dở)
    assert (await login(client, "doi_mk_dai")).status_code == 200


@pytest.mark.needs_pg
async def test_login_with_overlong_password_is_401_not_500(client, db, mailer):
    await register_verified(client, db, mailer, username="dang_nhap_dai")
    r = await login(client, "dang_nhap_dai", password="z" * 200)
    assert r.status_code == 401


# ═══ 2. dash_admin không được tác động lên admin ════════════════════════════════

async def _pair(client, db, mailer):
    admin = await token_for(client, db, mailer, username="quan_tri_chinh", role="admin")
    dash = await token_for(client, db, mailer, username="quan_tri_phu", role="dash_admin")
    return admin, dash


@pytest.mark.needs_pg
async def test_dash_admin_cannot_suspend_edit_or_delete_an_admin(client, db, mailer):
    admin, dash = await _pair(client, db, mailer)
    calls = [
        ("PATCH", f"/v1/admin/users/{admin['id']}/status", {"status": "suspended"}),
        ("PATCH", f"/v1/admin/users/{admin['id']}", {"email": "chiem.quyen@example.com"}),
        ("PATCH", f"/v1/admin/users/{admin['id']}", {"username": "bi_chiem"}),
        ("PATCH", f"/v1/admin/users/{admin['id']}", {"role": "viewer"}),
        ("DELETE", f"/v1/admin/users/{admin['id']}", None),
    ]
    for method, path, body in calls:
        r = await client.request(method, path, headers=dash["auth"], **({"json": body} if body else {}))
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["detail"] == "Chỉ quản trị viên (admin) mới được thao tác trên tài khoản admin"
        assert r.json().get("code") == "ADMIN_TARGET_FORBIDDEN"
    async with db.connection() as conn:  # admin còn nguyên
        row = await conn.fetchrow("SELECT username, email, role, status FROM accounts WHERE id=$1", admin["id"])
    assert (row["username"], row["email"], row["role"], row["status"]) == \
        ("quan_tri_chinh", "quan_tri_chinh@example.com", "admin", "active")
    assert (await login(client, "quan_tri_chinh")).status_code == 200


@pytest.mark.needs_pg
async def test_dash_admin_still_manages_normal_users_and_admin_manages_admins(client, db, mailer):
    admin, dash = await _pair(client, db, mailer)
    user = await token_for(client, db, mailer, username="nguoi_thuong")
    other_admin = await token_for(client, db, mailer, username="admin_thu_hai", role="admin")
    h = dash["auth"]
    assert (await client.patch(f"/v1/admin/users/{user['id']}/status", headers=h,
                               json={"status": "suspended"})).status_code == 200
    assert (await client.patch(f"/v1/admin/users/{user['id']}", headers=h,
                               json={"email": "moi@example.com"})).status_code == 200
    assert (await client.delete(f"/v1/admin/users/{user['id']}", headers=h)).status_code == 200
    # admin vẫn tác động được lên admin khác
    assert (await client.patch(f"/v1/admin/users/{other_admin['id']}/status", headers=admin["auth"],
                               json={"status": "suspended"})).status_code == 200
    assert (await client.delete(f"/v1/admin/users/{other_admin['id']}", headers=admin["auth"])).status_code == 200
    # tài khoản không tồn tại vẫn là 404 (không lộ khác biệt gì thêm)
    assert (await client.delete("/v1/admin/users/khong-co", headers=h)).status_code == 404


# ═══ 3. Audience access token OIDC ═════════════════════════════════════════════

async def _third_party_tokens(client, db, *, audience="tp-api", username="nguoi_dung"):
    """Client confidential; authorize khi còn first_party (bỏ consent) rồi hạ xuống third-party trước khi đổi code."""
    secret = await make_client(db, "tp-app", ctype="confidential", audience=audience, first_party=True)
    await make_account(db, username)
    await sso_login(client, username)
    code = await get_code(client, authz_params("tp-app"))
    async with db.connection() as conn:
        await conn.execute("UPDATE oidc_clients SET first_party=false WHERE client_id='tp-app'")
    r = await exchange(client, code=code, verifier=None, client_id="tp-app", secret=secret)
    assert r.status_code == 200, r.text
    return secret, r.json()


def _sdk_verifier(client) -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, audience="manager-api", jwks_url="http://identity.test/.well-known/jwks.json",
                         http_client=client)


@pytest.mark.needs_pg
async def test_third_party_access_token_has_only_own_audience_and_platform_verifier_rejects_it(client, db):
    import jwt
    secret, t = await _third_party_tokens(client, db)
    claims = jwt.decode(t["access_token"], options={"verify_signature": False})
    assert claims["aud"] == ["tp-api"] and "manager-api" not in claims["aud"]
    with pytest.raises(AuthError):
        await _sdk_verifier(client).verify(t["access_token"])
    # API nền tảng của chính Identity cũng từ chối
    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {t['access_token']}"})
    assert me.status_code == 401
    admin_api = await client.get("/v1/admin/users", headers={"Authorization": f"Bearer {t['access_token']}"})
    assert admin_api.status_code == 401


@pytest.mark.needs_pg
async def test_third_party_audience_defaults_to_client_id_and_cannot_claim_platform_audience(client, db):
    import jwt
    _, t = await _third_party_tokens(client, db, audience="")
    assert jwt.decode(t["access_token"], options={"verify_signature": False})["aud"] == ["tp-app"]
    async with db.connection() as conn:
        await conn.execute("DELETE FROM oidc_clients")
    # client tự đặt audience = audience nền tảng: bị bỏ qua, quay về client_id
    _, t2 = await _third_party_tokens(client, db, audience="manager-api", username="nguoi_dung2")
    aud = jwt.decode(t2["access_token"], options={"verify_signature": False})["aud"]
    assert aud == ["tp-app"]
    with pytest.raises(AuthError):
        await _sdk_verifier(client).verify(t2["access_token"])


@pytest.mark.needs_pg
async def test_userinfo_introspect_revoke_still_work_for_third_party_token(client, db):
    secret, t = await _third_party_tokens(client, db)
    ui = await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {t['access_token']}"})
    assert ui.status_code == 200 and ui.json()["sub"]
    r = await client.post("/oauth/introspect", headers=basic_header("tp-app", secret),
                          data={"token": t["access_token"]})
    body = r.json()
    assert body["active"] is True and body["aud"] == ["tp-api"] and body["client_id"] == "tp-app"
    rv = await client.post("/oauth/revoke", headers=basic_header("tp-app", secret), data={"token": t["access_token"]})
    assert rv.status_code == 200
    r = await client.post("/oauth/introspect", headers=basic_header("tp-app", secret),
                          data={"token": t["access_token"]})
    assert r.json() == {"active": False}


@pytest.mark.needs_pg
async def test_first_party_token_keeps_platform_audience_and_passes_verifier(client, db):
    import jwt
    await make_client(db)  # first_party mặc định
    await make_account(db, "nguoi_dung")
    t = await full_login(client, db, "nguoi_dung")
    assert set(jwt.decode(t["access_token"], options={"verify_signature": False})["aud"]) == {"manager-api", "app1-api"}
    principal = await _sdk_verifier(client).verify(t["access_token"])
    assert principal is not None


@pytest.mark.needs_pg
async def test_id_token_is_not_accepted_by_userinfo(client, db):
    """any_audience chỉ nới aud: id_token (typ JWT) vẫn không dùng được như access token."""
    await make_client(db)
    await make_account(db, "nguoi_dung")
    t = await full_login(client, db, "nguoi_dung")
    r = await client.get("/oauth/userinfo", headers={"Authorization": f"Bearer {t['id_token']}"})
    assert r.status_code == 401


# ═══ 4a. Đổi email => xác thực lại ═════════════════════════════════════════════

@pytest.mark.needs_pg
async def test_changing_email_clears_verified_flag_and_sends_verification(client, db, mailer):
    t = await token_for(client, db, mailer, username="doi_email")
    before = len(mailer.sent)
    r = await client.patch("/v1/auth/profile", headers=t["auth"], json={"email": "Email.Moi@example.com"})
    assert r.status_code == 200 and r.json()["updates"] == {"email": "email.moi@example.com"}
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT email, email_verified, status FROM accounts WHERE id=$1", t["id"])
        events = await conn.fetch("SELECT type FROM outbox WHERE type='account.profile_updated'")
    assert (row["email"], row["email_verified"], row["status"]) == ("email.moi@example.com", False, "active")
    assert len(mailer.sent) == before + 1
    kind, to, _, _ = mailer.sent[-1]
    assert (kind, to) == ("verify", "email.moi@example.com")
    assert len(events) == 1
    # đăng nhập vẫn được (status active); bấm link thì verified lại
    assert (await login(client, "doi_email")).status_code == 200
    assert (await client.get("/v1/auth/verify-email", params={"token": mailer.last("verify")})).status_code == 200
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT email_verified FROM accounts WHERE id=$1", t["id"]) is True


@pytest.mark.needs_pg
async def test_username_only_change_keeps_email_verified(client, db, mailer):
    t = await token_for(client, db, mailer, username="doi_ten_thoi")
    before = len(mailer.sent)
    r = await client.patch("/v1/auth/profile", headers=t["auth"], json={"username": "ten_moi_xyz"})
    assert r.status_code == 200
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT email_verified FROM accounts WHERE id=$1", t["id"]) is True
    assert len(mailer.sent) == before


# ═══ 4b. Rate limiter _gc ═══════════════════════════════════════════════════════

def test_rate_limiter_gc_keeps_keys_with_longer_window():
    class Clk:
        t = 1000.0

        def __call__(self):
            return self.t

    clk = Clk()
    rl = RateLimiter(clk)
    rl.check("9.9.9.9", "register", 1, 3600)   # cửa sổ 1 giờ, đã chạm giới hạn
    for i in range(10_001):                      # làm bảng vượt ngưỡng dọn (10_000 khóa) bằng khóa cửa sổ ngắn
        rl.check(f"10.0.{i // 250}.{i % 250}", "login", 5, 60)
    clk.t += 120                                 # khóa cửa sổ 60s đã hết hạn, khóa 3600s thì chưa
    rl.check("8.8.8.8", "login", 5, 60)          # kích hoạt _gc (trước fix dùng cửa sổ 60s cho MỌI khóa)
    with pytest.raises(Exception) as exc:
        rl.check("9.9.9.9", "register", 1, 3600)
    assert getattr(exc.value, "status_code", None) == 429, "khóa cửa sổ dài bị dọn nhầm"
    assert len(rl._hits) < 10_000                # khóa ngắn đã hết hạn thì được dọn
