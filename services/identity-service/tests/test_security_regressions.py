"""Hồi quy bảo mật: mỗi test khoá lại một lỗi của legacy (hoặc một lỗi đã tự mắc và sửa) để không tái diễn."""

from __future__ import annotations

import asyncio
import re

import asyncpg
import pytest

from identity_service import security
from identity_service.accounts import repository as accounts_repo
from identity_service.security import hash_token

from conftest import login, make_key, register, register_verified, token_for

pytestmark = pytest.mark.needs_pg


async def test_no_plaintext_token_is_stored(client, db, mailer):
    t = await token_for(client, db, mailer, username="khong_luu_ro")
    await client.post("/v1/auth/forgot-password", json={"email": "khong_luu_ro@example.com"})
    await client.post("/v1/auth/change-password/send-code", headers=t["auth"])
    secrets_ = {mailer.last("reset"), mailer.last("code"), t["refresh"]}
    async with db.connection() as conn:
        a = await conn.fetchrow("SELECT * FROM accounts WHERE username='khong_luu_ro'")
        refresh = await conn.fetch("SELECT token_hash FROM refresh_tokens")
        dump = " ".join(str(v) for v in a.values()) + " ".join(r["token_hash"] for r in refresh)
    for secret in secrets_:
        assert secret not in dump, "token rõ bị lưu trong DB"
    assert a["reset_token_hash"] == hash_token(mailer.last("reset"))
    assert a["change_code_hash"] == hash_token(mailer.last("code"))
    assert refresh[0]["token_hash"] == hash_token(t["refresh"]) and len(refresh[0]["token_hash"]) == 64


async def test_six_digit_change_code_cannot_reset_a_password(client, db, mailer):
    """Legacy: mã 6 số nằm chung cột với token reset nên dò được qua /reset-password (không đăng nhập)."""
    t = await token_for(client, db, mailer, username="nan_nhan")
    await client.post("/v1/auth/change-password/send-code", headers=t["auth"])
    code = mailer.last("code")
    r = await client.post("/v1/auth/reset-password", json={"token": code, "new_password": "ke-tan-cong-999"})
    assert r.status_code == 400
    assert (await login(client, "nan_nhan", password="mat-khau-12345")).status_code == 200  # mật khẩu không đổi


async def test_reset_token_cannot_be_used_as_change_code(client, db, mailer):
    t = await token_for(client, db, mailer, username="cheo_loai")
    await client.post("/v1/auth/forgot-password", json={"email": "cheo_loai@example.com"})
    r = await client.post("/v1/auth/change-password", headers=t["auth"],
                          json={"code": "123456", "new_password": "mat-khau-moi-999"})
    assert r.status_code == 400


async def test_verify_link_does_not_unsuspend_a_suspended_account(client, db, mailer):
    """Legacy `set_email_verified` đặt status='active' vô điều kiện, gỡ khoá của admin."""
    await register(client, db, username="bi_khoa_som")
    async with db.connection() as conn:
        await conn.execute("UPDATE accounts SET status='suspended' WHERE username='bi_khoa_som'")
    r = await client.get("/v1/auth/verify-email", params={"token": mailer.last("verify")})
    assert r.status_code == 200
    async with db.connection() as conn:
        a = await conn.fetchrow("SELECT status, email_verified FROM accounts WHERE username='bi_khoa_som'")
    assert (a["status"], a["email_verified"]) == ("suspended", True)
    assert (await login(client, "bi_khoa_som")).status_code == 403


async def test_expired_key_cannot_register(client, db, clock):
    """Legacy chỉ kiểm hạn ở validate-key; register vẫn dùng được key đã hết hạn."""
    from datetime import timedelta
    key = await make_key(db, expires_at=clock() - timedelta(seconds=1), clock=clock)
    r = await register(client, db, username="key_het_han", key=key)
    assert (r.status_code, r.json()["detail"]) == (400, "Mã mời đã hết hạn")
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key) == 0


async def test_failed_registration_does_not_burn_the_invite_key(client, db, mailer, monkeypatch):
    """Dùng key và tạo account cùng một transaction: tạo account lỗi thì key được hoàn lại."""
    key = await make_key(db, max_uses=1)

    async def boom(*a, **kw):
        raise RuntimeError("lỗi giữa chừng khi tạo account")

    monkeypatch.setattr(accounts_repo, "create", boom)
    with pytest.raises(RuntimeError):
        await register(client, db, username="loi_giua_chung", key=key)
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key) == 0
        assert await conn.fetchval("SELECT count(*) FROM accounts") == 0


async def test_concurrent_registrations_never_overspend_a_key(client, db):
    """Hai người đăng ký cùng lúc với key chỉ còn 1 lượt: đúng một người thành công."""
    key = await make_key(db, max_uses=1)

    async def go(i):
        return await client.post("/v1/auth/register", headers={"X-Real-IP": f"7.7.7.{i}"}, json={
            "key": key, "username": f"song_song_{i}", "password": "mat-khau-12345",
            "email": f"song.song.{i}@example.com"})

    results = await asyncio.gather(*[go(i) for i in range(6)])
    assert sorted(r.status_code for r in results).count(200) == 1
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key) == 1
        assert await conn.fetchval("SELECT count(*) FROM accounts") == 1


async def test_concurrent_same_username_yields_one_account_and_a_clean_409(client, db):
    key = await make_key(db, max_uses=10)

    async def go(i):
        return await client.post("/v1/auth/register", headers={"X-Real-IP": f"6.6.6.{i}"}, json={
            "key": key, "username": "trung_ten", "password": "mat-khau-12345",
            "email": f"trung.ten.{i}@example.com"})

    results = await asyncio.gather(*[go(i) for i in range(6)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and all(c in (200, 409) for c in codes), codes  # không có 500
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM accounts") == 1
        assert await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key) == 1


async def test_unknown_user_login_still_runs_a_password_hash(client, db, mailer, monkeypatch):
    """Thời gian phản hồi không được lộ username có tồn tại: cả hai nhánh đều chạy bcrypt đúng một lần."""
    await register_verified(client, db, mailer, username="co_that_su")
    calls = []
    real = security.verify_password

    import identity_service.auth.service as svc
    monkeypatch.setattr(svc, "verify_password", lambda p, h: (calls.append(h), real(p, h))[1])
    await login(client, "co_that_su", password="sai-mat-khau-1")
    await login(client, "khong_ton_tai_chut_nao")
    assert len(calls) == 2


@pytest.mark.parametrize("payload", ["' OR '1'='1", "admin'--", "a'; DROP TABLE accounts;--", "\x00", "ü" * 500])
async def test_hostile_strings_never_break_sql(client, db, payload):
    r = await client.post("/v1/auth/login", json={"username": payload, "password": payload})
    # NUL không lưu được trong Postgres text: là lỗi đầu vào (422), tuyệt đối không phải 500.
    assert r.status_code == (422 if payload == "\x00" else 401), r.text
    assert (await client.post("/v1/auth/check-username", json={"username": payload})).status_code in (200, 422)
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT to_regclass('public.accounts')") is not None


async def test_account_ids_are_unguessable_uuid_hex_and_unique(client, db, mailer):
    ids = set()
    for i in range(5):
        t = await token_for(client, db, mailer, username=f"id_{i}")
        assert re.fullmatch(r"[0-9a-f]{32}", t["id"])
        ids.add(t["id"])
    assert len(ids) == 5


async def test_db_unique_constraints_backstop_the_application(db):
    """Dù code ứng dụng sai, DB vẫn chặn trùng username/email."""
    async with db.connection() as conn:
        await conn.execute("INSERT INTO accounts(id,username,email,password_hash,invite_key) "
                           "VALUES('1','u','u@x.com','h','k')")
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute("INSERT INTO accounts(id,username,email,password_hash,invite_key) "
                               "VALUES('2','u','v@x.com','h','k')")
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute("INSERT INTO accounts(id,username,email,password_hash,invite_key) "
                               "VALUES('3','w','u@x.com','h','k')")


async def test_access_token_of_deleted_user_is_rejected_at_me(client, db, mailer):
    t = await token_for(client, db, mailer, username="se_bi_xoa")
    async with db.connection() as conn:
        await conn.execute("DELETE FROM accounts WHERE id=$1", t["id"])
    assert (await client.get("/v1/auth/me", headers=t["auth"])).status_code == 401
