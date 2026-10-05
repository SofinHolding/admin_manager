"""Luồng xác thực đầy đủ trên Postgres thật, qua HTTP (ASGI) — hành vi nhìn thấy từ frontend."""

from __future__ import annotations

import re

import pytest

from identity_service.auth import legacy_jwt
from identity_service.security import hash_token

from conftest import TEST_JWT_SECRET, claims, login, make_key, register, register_verified, token_for

pytestmark = pytest.mark.needs_pg

SECRET = TEST_JWT_SECRET


# ── validate-key / check ────────────────────────────────────────────────────────

async def test_validate_key_all_outcomes(client, db, clock):
    ok = await make_key(db, role="admin")
    revoked = await make_key(db, status="revoked")
    used_up = await make_key(db, max_uses=1)
    expired = await make_key(db, expires_at=clock() - __import__("datetime").timedelta(days=1), clock=clock)
    await register(client, db, username="dung_het", key=used_up)

    async def v(key):
        return (await client.post("/v1/auth/validate-key", json={"key": key})).json()

    assert await v(ok) == {"valid": True, "role": "admin"}
    assert await v("KHONG-TON") == {"valid": False, "reason": "Mã mời không hợp lệ"}
    assert await v(revoked) == {"valid": False, "reason": "Mã mời đã bị vô hiệu"}
    assert await v(used_up) == {"valid": False, "reason": "Mã mời đã hết lượt sử dụng"}
    assert await v(expired) == {"valid": False, "reason": "Mã mời đã hết hạn"}
    assert await v(f"  {ok}  ") == {"valid": True, "role": "admin"}  # tự cắt khoảng trắng


async def test_check_username_and_email_availability(client, db):
    await register(client, db, username="nguoi_dung", email="Nguoi.Dung@Example.com")
    assert (await client.post("/v1/auth/check-username", json={"username": "nguoi_dung"})).json() == {"available": False}
    assert (await client.post("/v1/auth/check-username", json={"username": "ten_moi"})).json() == {"available": True}
    # email không phân biệt hoa/thường
    assert (await client.post("/v1/auth/check-email", json={"email": "NGUOI.DUNG@example.COM"})).json() == {"available": False}
    assert (await client.post("/v1/auth/check-email", json={"email": "khac@example.com"})).json() == {"available": True}


# ── đăng ký ─────────────────────────────────────────────────────────────────────

async def test_register_creates_pending_account_and_sends_verify_mail(client, db, mailer):
    r = await register(client, db, username="user_one", email="User.One@Example.com")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "email": "user.one@example.com",
                        "message": "Đã gửi email xác thực. Kiểm tra hộp thư để kích hoạt tài khoản."}
    kind, email, token, username = mailer.sent[-1]
    assert (kind, email, username) == ("verify", "user.one@example.com", "user_one") and len(token) == 48
    async with db.connection() as conn:
        a = await conn.fetchrow("SELECT * FROM accounts WHERE username='user_one'")
    assert a["status"] == "pending" and a["email_verified"] is False and re.fullmatch(r"[0-9a-f]{32}", a["id"])
    assert a["role"] == "viewer"


async def test_register_error_messages_and_codes(client, db):
    good = await make_key(db, max_uses=10)
    body = {"key": good, "username": "ten_dung", "password": "mat-khau-12345", "email": "a@example.com"}

    async def post(i, **override):  # mỗi lần một IP riêng: register giới hạn 3 lần/giờ/IP
        return await client.post("/v1/auth/register", json={**body, **override},
                                 headers={"X-Real-IP": f"8.8.8.{i}"})

    assert (await post(1, key="SAI-KEY1")).json()["detail"] == "Mã mời không hợp lệ hoặc đã hết lượt"
    assert (await post(2)).status_code == 200
    r = await post(3, email="b@example.com")
    assert (r.status_code, r.json()["detail"]) == (409, "Tên đăng nhập đã được sử dụng")
    r = await post(4, username="ten_khac", email="A@example.com")
    assert (r.status_code, r.json()["detail"]) == (409, "Email đã được đăng ký")


@pytest.mark.parametrize("override", [
    {"username": "ab"}, {"username": "co dau cach"}, {"username": "x" * 31},
    {"password": "ngan"}, {"email": "khong-phai-email"},
])
async def test_register_input_validation_is_422_without_echoing_values(client, db, override):
    key = await make_key(db)
    body = {"key": key, "username": "hop_le", "password": "mat-khau-12345", "email": "ok@example.com",
            **override}
    r = await client.post("/v1/auth/register", json=body)
    assert r.status_code == 422
    for secret in ("mat-khau-12345", "ngan", "khong-phai-email"):
        assert secret not in r.text


async def test_register_consumes_one_use_and_exhausted_key_is_rejected(client, db):
    key = await make_key(db, max_uses=1)
    assert (await register(client, db, username="nguoi_mot", key=key)).status_code == 200
    r = await register(client, db, username="nguoi_hai", key=key)
    assert (r.status_code, r.json()["detail"]) == (400, "Mã mời không hợp lệ hoặc đã hết lượt")
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key) == 1


async def test_register_gives_account_the_role_of_the_key(client, db):
    await register(client, db, username="nguoi_admin", role="dash_admin")
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT role FROM accounts WHERE username='nguoi_admin'") == "dash_admin"


async def test_mail_failure_does_not_fail_registration(client, db, mailer):
    mailer.fail = True
    assert (await register(client, db, username="mail_hong")).status_code == 200


# ── xác thực email ──────────────────────────────────────────────────────────────

async def test_verify_email_activates_account_and_link_is_single_use(client, db, mailer):
    await register(client, db, username="can_xac_thuc")
    token = mailer.last("verify")
    r = await client.get("/v1/auth/verify-email", params={"token": token})
    assert r.json() == {"ok": True, "message": "Xác thực email thành công. Đăng nhập ngay."}
    async with db.connection() as conn:
        a = await conn.fetchrow("SELECT status, email_verified FROM accounts WHERE username='can_xac_thuc'")
    assert (a["status"], a["email_verified"]) == ("active", True)
    again = await client.get("/v1/auth/verify-email", params={"token": token})
    assert (again.status_code, again.json()["detail"]) == (400, "Link xác thực không hợp lệ hoặc đã được sử dụng")


async def test_verify_email_link_expires_after_24h(client, db, mailer, clock):
    await register(client, db, username="het_han", clock=clock)
    clock.advance(hours=24, seconds=1)
    r = await client.get("/v1/auth/verify-email", params={"token": mailer.last("verify")})
    assert (r.status_code, r.json()["detail"]) == (400, "Link xác thực đã hết hạn. Đăng nhập và yêu cầu gửi lại.")


async def test_verify_with_unknown_or_empty_token(client):
    assert (await client.get("/v1/auth/verify-email", params={"token": "khong-co"})).status_code == 400
    assert (await client.get("/v1/auth/verify-email", params={"token": ""})).status_code == 400
    assert (await client.get("/v1/auth/verify-email")).status_code == 422


async def test_resend_verify_is_generic_and_issues_a_new_token(client, db, mailer):
    generic = {"ok": True, "message": "Nếu email tồn tại và chưa xác thực, đã gửi lại."}
    await register(client, db, username="gui_lai", email="gui.lai@example.com")
    old = mailer.last("verify")
    assert (await client.post("/v1/auth/resend-verify", json={"email": "gui.lai@example.com"})).json() == generic
    new = mailer.last("verify")
    assert new != old
    # email không tồn tại: cùng câu trả lời, không gửi mail
    n = len(mailer.sent)
    assert (await client.post("/v1/auth/resend-verify", json={"email": "khong.co@example.com"})).json() == generic
    assert len(mailer.sent) == n
    # token cũ không còn dùng được
    assert (await client.get("/v1/auth/verify-email", params={"token": old})).status_code == 400
    assert (await client.get("/v1/auth/verify-email", params={"token": new})).status_code == 200
    # đã xác thực: không gửi nữa
    n = len(mailer.sent)
    await client.post("/v1/auth/resend-verify", json={"email": "gui.lai@example.com"})
    assert len(mailer.sent) == n


# ── đăng nhập ───────────────────────────────────────────────────────────────────

async def test_login_before_verification_is_forbidden(client, db):
    await register(client, db, username="chua_xac_thuc")
    r = await login(client, "chua_xac_thuc")
    assert r.status_code == 403 and r.json()["detail"].startswith("Tài khoản chưa xác thực email.")


async def test_login_success_response_shape_and_token_claims(client, db, mailer):
    await register_verified(client, db, mailer, username="dang_nhap")
    r = await login(client, "dang_nhap")
    body = r.json()
    assert r.status_code == 200 and set(body) == {"access_token", "refresh_token", "expires_in", "user"}
    assert body["expires_in"] == 900 and set(body["user"]) == {"id", "username", "email", "role"}
    c = await claims(client, body["access_token"])
    assert c["sub"] == body["user"]["id"] and c["role"] == "viewer" and c["client_type"] == "web"
    assert c["iss"] == "http://identity.test" and c["aud"] == ["manager-api"] and c["perms"] == []
    assert c["sid"] and c["jti"] and c["exp"] - c["iat"] == 900 and c["nbf"] == c["iat"]
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT last_login FROM accounts WHERE username='dang_nhap'") is not None


async def test_login_wrong_password_and_unknown_user_are_indistinguishable_and_delayed(client, db, mailer, sleeps):
    await register_verified(client, db, mailer, username="co_that")
    bad = await login(client, "co_that", password="sai-mat-khau-1")
    unknown = await login(client, "khong_ton_tai")
    assert (bad.status_code, bad.json()) == (unknown.status_code, unknown.json()) == (
        401, {"detail": "Tên đăng nhập hoặc mật khẩu không đúng"})
    assert len(sleeps) == 2  # có delay cho CẢ HAI trường hợp (chống dò username)


async def test_login_suspended_account_is_forbidden(client, db, mailer):
    await register_verified(client, db, mailer, username="bi_khoa")
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended' WHERE username='bi_khoa'")
    r = await login(client, "bi_khoa")
    assert (r.status_code, r.json()["detail"]) == (403, "Tài khoản đã bị khoá. Liên hệ quản trị viên.")


async def test_login_wrong_password_for_suspended_does_not_reveal_status(client, db, mailer):
    await register_verified(client, db, mailer, username="bi_khoa2")
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended' WHERE username='bi_khoa2'")
    assert (await login(client, "bi_khoa2", password="sai-mat-khau-1")).status_code == 401


async def test_fallback_tool_login_only_for_admin_roles_and_is_audited(client, db, mailer):
    await register_verified(client, db, mailer, username="nguoi_xem", role="viewer")
    await register_verified(client, db, mailer, username="quan_tri", role="dash_admin")
    h = {"X-Client-Type": "local_fallback_tool"}
    r = await login(client, "nguoi_xem", **h)
    assert (r.status_code, r.json()["detail"]) == (403, "Tool B chỉ cho phép tài khoản admin hoặc dash_admin đăng nhập")
    ok = await login(client, "quan_tri", **h)
    assert ok.status_code == 200
    assert (await claims(client, ok.json()["access_token"]))["client_type"] == "local_fallback_tool"
    async with db.connection() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM auth_audit WHERE event_type='login_fallback_tool'") == 1
    # header lạ hoặc thiếu -> coi là web
    other = await login(client, "nguoi_xem", **{"X-Client-Type": "gi-do-la"})
    assert other.status_code == 200
    assert (await claims(client, other.json()["access_token"]))["client_type"] == "web"


# ── refresh / logout / me ───────────────────────────────────────────────────────

async def test_refresh_logout_cycle(client, db, mailer):
    t = await token_for(client, db, mailer, username="vong_doi")
    r = await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})
    assert r.status_code == 200 and set(r.json()) == {"access_token", "expires_in"}
    assert (await client.post("/v1/auth/logout", json={"refresh_token": t["refresh"]})).json() == {"ok": True}
    after = await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})
    assert (after.status_code, after.json()["detail"]) == (401, "Refresh token không hợp lệ hoặc đã hết hạn")
    # logout với token không tồn tại vẫn ok (fire-and-forget)
    assert (await client.post("/v1/auth/logout", json={"refresh_token": "khong-co"})).json() == {"ok": True}


async def test_refresh_token_expires_after_30_days(client, db, mailer, clock):
    t = await token_for(client, db, mailer, username="het_30_ngay", clock=clock)
    clock.advance(days=30, seconds=1)
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})).status_code == 401


async def test_refresh_fails_for_suspended_account(client, db, mailer):
    t = await token_for(client, db, mailer, username="refresh_khoa")
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended' WHERE id=$1", t["id"])
    r = await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})
    assert (r.status_code, r.json()["detail"]) == (401, "Tài khoản không khả dụng")


async def test_me_returns_profile_and_enforces_auth(client, db, mailer):
    t = await token_for(client, db, mailer, username="toi_la")
    r = await client.get("/v1/auth/me", headers=t["auth"])
    body = r.json()
    assert r.status_code == 200 and set(body) == {"id", "username", "email", "role", "created_at"}
    assert body["created_at"].endswith("+00:00")
    assert (await client.get("/v1/auth/me")).json() == {"detail": "Thiếu token"}
    assert (await client.get("/v1/auth/me", headers={"Authorization": "Bearer rac"})).status_code == 401
    assert (await client.get("/v1/auth/me", headers={"Authorization": "Basic abc"})).status_code == 401


async def test_me_for_suspended_is_403_and_for_deleted_is_401(client, db, mailer):
    t = await token_for(client, db, mailer, username="doi_trang_thai")
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended' WHERE id=$1", t["id"])
    assert (await client.get("/v1/auth/me", headers=t["auth"])).status_code == 403
    async with db.connection() as conn:
        await conn.execute("DELETE FROM accounts WHERE id=$1", t["id"])
    r = await client.get("/v1/auth/me", headers=t["auth"])
    # RS256: phiên bị xóa cùng tài khoản nên token bị từ chối ở bước kiểm phiên (HS256 legacy: "Tài khoản không tồn tại")
    assert (r.status_code, r.json()["detail"]) == (401, "Phiên đã bị thu hồi")


# ── quên / đặt lại mật khẩu ─────────────────────────────────────────────────────

async def test_forgot_password_is_generic_and_only_mails_active_accounts(client, db, mailer):
    generic = {"ok": True, "message": "Nếu email tồn tại, đã gửi link đặt lại mật khẩu."}
    await register_verified(client, db, mailer, username="quen_mk")
    await register(client, db, username="chua_kich_hoat")  # pending
    n = len(mailer.sent)
    assert (await client.post("/v1/auth/forgot-password", json={"email": "khong.co@example.com"})).json() == generic
    assert (await client.post("/v1/auth/forgot-password", json={"email": "chua_kich_hoat@example.com"})).json() == generic
    assert len(mailer.sent) == n
    assert (await client.post("/v1/auth/forgot-password", json={"email": "quen_mk@example.com"})).json() == generic
    assert mailer.sent[-1][0] == "reset"


async def test_reset_password_flow_revokes_sessions_and_token_is_single_use(client, db, mailer):
    t = await token_for(client, db, mailer, username="dat_lai")
    await client.post("/v1/auth/forgot-password", json={"email": "dat_lai@example.com"})
    token = mailer.last("reset")
    r = await client.post("/v1/auth/reset-password", json={"token": token, "new_password": "mat-khau-moi-999"})
    assert r.json() == {"ok": True, "message": "Mật khẩu đã đặt lại. Đăng nhập với mật khẩu mới."}
    assert (await login(client, "dat_lai", password="mat-khau-12345")).status_code == 401
    assert (await login(client, "dat_lai", password="mat-khau-moi-999")).status_code == 200
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})).status_code == 401
    again = await client.post("/v1/auth/reset-password", json={"token": token, "new_password": "mat-khau-khac-1"})
    assert (again.status_code, again.json()["detail"]) == (400, "Link đặt lại mật khẩu không hợp lệ hoặc đã được sử dụng")


async def test_reset_token_expires_after_1h(client, db, mailer, clock):
    await token_for(client, db, mailer, username="reset_het_han", clock=clock)
    await client.post("/v1/auth/forgot-password", json={"email": "reset_het_han@example.com"})
    clock.advance(hours=1, seconds=1)
    r = await client.post("/v1/auth/reset-password",
                          json={"token": mailer.last("reset"), "new_password": "mat-khau-moi-999"})
    assert (r.status_code, r.json()["detail"]) == (400, "Link đặt lại mật khẩu đã hết hạn. Yêu cầu gửi lại.")


# ── đổi mật khẩu bằng mã 6 số ───────────────────────────────────────────────────

async def test_change_password_with_email_code(client, db, mailer):
    t = await token_for(client, db, mailer, username="doi_mk")
    assert (await client.post("/v1/auth/change-password/send-code")).status_code == 401  # cần đăng nhập
    r = await client.post("/v1/auth/change-password/send-code", headers=t["auth"])
    assert r.json() == {"ok": True, "message": "Đã gửi mã xác thực về email của bạn."}
    code = mailer.last("code")
    assert mailer.sent[-1][0] == "code" and re.fullmatch(r"\d{6}", code)  # có mẫu email riêng cho mã
    r = await client.post("/v1/auth/change-password", headers=t["auth"],
                          json={"code": code, "new_password": "mat-khau-moi-999"})
    assert r.json() == {"ok": True, "message": "Mật khẩu đã thay đổi. Đăng nhập lại với mật khẩu mới."}
    assert (await login(client, "doi_mk", password="mat-khau-moi-999")).status_code == 200
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})).status_code == 401


async def test_change_password_without_requesting_code(client, db, mailer):
    t = await token_for(client, db, mailer, username="khong_xin_ma")
    r = await client.post("/v1/auth/change-password", headers=t["auth"],
                          json={"code": "123456", "new_password": "mat-khau-moi-999"})
    assert (r.status_code, r.json()["detail"]) == (400, "Chưa yêu cầu mã xác thực. Gửi mã trước.")


async def test_change_code_expires_after_10_minutes(client, db, mailer, clock):
    t = await token_for(client, db, mailer, username="ma_het_han", clock=clock)
    await client.post("/v1/auth/change-password/send-code", headers=t["auth"])
    code = mailer.last("code")
    clock.advance(minutes=10, seconds=1)
    r = await client.post("/v1/auth/change-password", headers=t["auth"],
                          json={"code": code, "new_password": "mat-khau-moi-999"})
    assert (r.status_code, r.json()["detail"]) == (400, "Mã xác thực đã hết hạn. Yêu cầu gửi lại.")


async def test_change_code_is_locked_after_five_wrong_attempts(client, db, mailer):
    """Chặn dò 10^6 mã: sai 5 lần thì mã bị huỷ, mã ĐÚNG cũng không còn dùng được."""
    t = await token_for(client, db, mailer, username="do_ma")
    await client.post("/v1/auth/change-password/send-code", headers=t["auth"])
    real = mailer.last("code")
    wrong = "000000" if real != "000000" else "111111"
    msgs = []
    for i in range(5):
        # đổi IP mỗi lần để rate limit theo IP không che mất giới hạn theo tài khoản
        r = await client.post("/v1/auth/change-password", headers={**t["auth"], "X-Real-IP": f"9.9.9.{i}"},
                              json={"code": wrong, "new_password": "mat-khau-moi-999"})
        msgs.append(r.json()["detail"])
    assert msgs[:4] == ["Mã xác thực không đúng"] * 4 and msgs[4] == "Quá nhiều lần nhập sai. Yêu cầu gửi lại mã."
    r = await client.post("/v1/auth/change-password", headers={**t["auth"], "X-Real-IP": "9.9.9.99"},
                          json={"code": real, "new_password": "mat-khau-moi-999"})
    assert (r.status_code, r.json()["detail"]) == (400, "Chưa yêu cầu mã xác thực. Gửi mã trước.")


async def test_change_password_code_format_is_validated(client, db, mailer):
    t = await token_for(client, db, mailer, username="dinh_dang_ma")
    for bad in ("12345", "1234567", "abcdef"):
        r = await client.post("/v1/auth/change-password", headers=t["auth"],
                              json={"code": bad, "new_password": "mat-khau-moi-999"})
        assert r.status_code == 422


# ── hồ sơ ───────────────────────────────────────────────────────────────────────

async def test_update_profile(client, db, mailer):
    t = await token_for(client, db, mailer, username="ho_so")
    await register(client, db, username="nguoi_khac", email="nguoi.khac@example.com")
    r = await client.patch("/v1/auth/profile", headers=t["auth"], json={})
    assert r.json() == {"ok": True, "message": "Không có thay đổi."}
    r = await client.patch("/v1/auth/profile", headers=t["auth"], json={"username": "nguoi_khac"})
    assert (r.status_code, r.json()["detail"]) == (409, "Tên đăng nhập đã được sử dụng")
    r = await client.patch("/v1/auth/profile", headers=t["auth"], json={"email": "Nguoi.Khac@example.com"})
    assert (r.status_code, r.json()["detail"]) == (409, "Email đã được đăng ký")
    r = await client.patch("/v1/auth/profile", headers=t["auth"],
                           json={"username": "ho_so_moi", "email": "Moi@Example.com"})
    assert r.json() == {"ok": True, "message": "Đã cập nhật hồ sơ.",
                        "updates": {"username": "ho_so_moi", "email": "moi@example.com"}}
    me = (await client.get("/v1/auth/me", headers=t["auth"])).json()
    assert (me["username"], me["email"]) == ("ho_so_moi", "moi@example.com")


# ── giới hạn tần suất ───────────────────────────────────────────────────────────

async def test_rate_limits_match_legacy(client, db, mailer):
    # login: 5 lần / 300s
    codes = [(await login(client, "khong_co", password="sai-mat-khau-1")).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    r = await login(client, "khong_co")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert "Quá nhiều lần thử. Đợi" in r.json()["detail"]
    # register: 3 lần / 3600s (kể cả lần thất bại)
    statuses = []
    for _ in range(4):
        statuses.append((await client.post("/v1/auth/register", json={
            "key": "SAI-KEY1", "username": "abcde", "password": "mat-khau-12345",
            "email": "a@example.com"})).status_code)
    assert statuses == [400, 400, 400, 429]
    # validate-key: 10 / 60s
    statuses = [(await client.post("/v1/auth/validate-key", json={"key": "x"})).status_code for _ in range(11)]
    assert statuses == [200] * 10 + [429]
    # check-username và check-email dùng chung bộ đếm 'check': 30 / 60s
    for _ in range(15):
        await client.post("/v1/auth/check-username", json={"username": "abc"})
    for _ in range(15):
        await client.post("/v1/auth/check-email", json={"email": "a@example.com"})
    assert (await client.post("/v1/auth/check-username", json={"username": "abc"})).status_code == 429


async def test_rate_limit_ip_comes_from_x_real_ip(client, db):
    for _ in range(5):
        await login(client, "khong_co", password="sai-mat-khau-1")
    assert (await login(client, "khong_co")).status_code == 429
    other = await client.post("/v1/auth/login", json={"username": "khong_co", "password": "x"},
                              headers={"X-Real-IP": "203.0.113.77"})
    assert other.status_code == 401  # IP khác thì chưa bị chặn
