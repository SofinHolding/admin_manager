"""API quản trị `/v1/admin/keys` và `/v1/admin/users` trên Postgres thật."""

from __future__ import annotations

import pytest

from conftest import login, make_key, register, token_for

pytestmark = pytest.mark.needs_pg


async def _admin(client, db, mailer, name="quan_tri", role="admin"):
    return await token_for(client, db, mailer, username=name, role=role)


# ── phân quyền ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/admin/keys"), ("POST", "/v1/admin/keys"), ("DELETE", "/v1/admin/keys/ABCD-EFGH"),
    ("GET", "/v1/admin/users"), ("PATCH", "/v1/admin/users/x/status"), ("PATCH", "/v1/admin/users/x"),
    ("DELETE", "/v1/admin/users/x"),
])
async def test_admin_endpoints_reject_anonymous_and_viewers(client, db, mailer, method, path):
    body = {"json": {}} if method in ("POST", "PATCH") else {}
    anon = await client.request(method, path, **body)
    assert anon.status_code == 401 and anon.json() == {"detail": "Thiếu token"}
    viewer = await token_for(client, db, mailer, username=f"viewer_{abs(hash(path)) % 10**6}")
    r = await client.request(method, path, headers=viewer["auth"], **body)
    assert (r.status_code, r.json()["detail"]) == (403, "Chỉ quản trị viên mới có quyền này")


# ── invite key ──────────────────────────────────────────────────────────────────

async def test_create_list_revoke_key(client, db, mailer):
    a = await _admin(client, db, mailer)
    r = await client.post("/v1/admin/keys", headers=a["auth"],
                          json={"role": "viewer", "label": "Team BR", "max_uses": 3, "expires_days": 30})
    key = r.json()
    assert r.status_code == 200 and re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", key["key"])
    assert set(key) == {"key", "role", "label", "max_uses", "used_count", "created_at", "expires_at", "status"}
    assert (key["role"], key["label"], key["max_uses"], key["used_count"], key["status"]) == \
        ("viewer", "Team BR", 3, 0, "active")
    listing = (await client.get("/v1/admin/keys", headers=a["auth"])).json()["keys"]
    assert any(k["key"] == key["key"] for k in listing)
    assert (await client.delete(f"/v1/admin/keys/{key['key']}", headers=a["auth"])).json() == {"ok": True}
    after = {k["key"]: k for k in (await client.get("/v1/admin/keys", headers=a["auth"])).json()["keys"]}
    assert after[key["key"]]["status"] == "revoked"
    # key bị thu hồi không đăng ký được
    r = await register(client, db, username="dung_key_thu_hoi", key=key["key"])
    assert r.status_code == 400
    nf = await client.delete("/v1/admin/keys/KHONG-TON", headers=a["auth"])
    assert (nf.status_code, nf.json()["detail"]) == (404, "Key không tồn tại")


import re  # noqa: E402  (đặt cuối để test đọc liền mạch)


@pytest.mark.parametrize("body", [
    {"role": "root"}, {"max_uses": 0}, {"max_uses": 1001}, {"expires_days": 0}, {"expires_days": 366},
    {"label": "x" * 201},
])
async def test_create_key_validation(client, db, mailer, body):
    a = await _admin(client, db, mailer)
    assert (await client.post("/v1/admin/keys", headers=a["auth"], json=body)).status_code == 422


async def test_dash_admin_cannot_mint_admin_keys_but_can_mint_others(client, db, mailer):
    d = await _admin(client, db, mailer, "dash_quan_tri", role="dash_admin")
    r = await client.post("/v1/admin/keys", headers=d["auth"], json={"role": "admin"})
    assert (r.status_code, r.json()["detail"]) == (403, "Chỉ quản trị viên (admin) mới được cấp quyền admin")
    for role in ("viewer", "discord", "dash_admin"):
        assert (await client.post("/v1/admin/keys", headers=d["auth"], json={"role": role})).status_code == 200


async def test_admin_can_mint_admin_key(client, db, mailer):
    a = await _admin(client, db, mailer)
    assert (await client.post("/v1/admin/keys", headers=a["auth"], json={"role": "admin"})).status_code == 200


async def test_created_key_defaults_match_legacy(client, db, mailer):
    a = await _admin(client, db, mailer)
    k = (await client.post("/v1/admin/keys", headers=a["auth"], json={})).json()
    assert (k["role"], k["max_uses"], k["label"]) == ("viewer", 1, "")


# ── quản lý user ────────────────────────────────────────────────────────────────

async def test_list_users_shape_and_order(client, db, mailer):
    a = await _admin(client, db, mailer)
    await register(client, db, username="nguoi_moi")
    users = (await client.get("/v1/admin/users", headers=a["auth"])).json()["users"]
    assert {u["username"] for u in users} == {"quan_tri", "nguoi_moi"}
    u = next(u for u in users if u["username"] == "quan_tri")
    assert set(u) == {"id", "username", "email", "role", "invite_key", "email_verified", "created_at",
                      "last_login", "status"}
    assert u["email_verified"] is True and u["last_login"].endswith("+00:00") and u["status"] == "active"
    never = next(u for u in users if u["username"] == "nguoi_moi")
    assert never["last_login"] == "" and never["email_verified"] is False  # chưa đăng nhập: chuỗi rỗng như legacy


async def test_suspend_and_unsuspend_user(client, db, mailer):
    a = await _admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="bi_khoa_boi_admin")
    r = await client.patch(f"/v1/admin/users/{u['id']}/status", headers=a["auth"], json={"status": "suspended"})
    assert r.json() == {"ok": True, "status": "suspended"}
    assert (await login(client, "bi_khoa_boi_admin")).status_code == 403
    assert (await client.get("/v1/auth/me", headers=u["auth"])).status_code == 403
    await client.patch(f"/v1/admin/users/{u['id']}/status", headers=a["auth"], json={"status": "active"})
    assert (await login(client, "bi_khoa_boi_admin")).status_code == 200


async def test_status_validation_self_lock_and_unknown_user(client, db, mailer):
    a = await _admin(client, db, mailer)
    me = a["id"]
    r = await client.patch(f"/v1/admin/users/{me}/status", headers=a["auth"], json={"status": "suspended"})
    assert (r.status_code, r.json()["detail"]) == (400, "Không thể khoá chính mình")
    assert (await client.patch("/v1/admin/users/abc/status", headers=a["auth"],
                               json={"status": "pending"})).status_code == 422
    r = await client.patch("/v1/admin/users/khong-co/status", headers=a["auth"], json={"status": "active"})
    assert (r.status_code, r.json()["detail"]) == (404, "Tài khoản không tồn tại")


async def test_update_user_fields_and_conflicts(client, db, mailer):
    a = await _admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="can_sua")
    other = await token_for(client, db, mailer, username="nguoi_giu_ten")
    r = await client.patch(f"/v1/admin/users/{u['id']}", headers=a["auth"], json={"username": "nguoi_giu_ten"})
    assert (r.status_code, r.json()["detail"]) == (409, "Tên đăng nhập đã được sử dụng")
    r = await client.patch(f"/v1/admin/users/{u['id']}", headers=a["auth"],
                           json={"email": "Nguoi_Giu_Ten@example.com"})
    assert (r.status_code, r.json()["detail"]) == (409, "Email đã được đăng ký")
    r = await client.patch(f"/v1/admin/users/{u['id']}", headers=a["auth"],
                           json={"username": "da_sua", "email": "DA.SUA@example.com", "role": "dash_admin"})
    assert r.json() == {"ok": True, "updates": {"username": "da_sua", "email": "da.sua@example.com",
                                                 "role": "dash_admin"}}
    assert (await client.patch(f"/v1/admin/users/{u['id']}", headers=a["auth"], json={})).json() == \
        {"ok": True, "message": "Không có thay đổi."}
    assert (await client.patch("/v1/admin/users/khong-co", headers=a["auth"],
                               json={"role": "viewer"})).status_code == 404
    # giữ nguyên username của chính mình khi gửi lại không bị coi là trùng
    assert (await client.patch(f"/v1/admin/users/{other['id']}", headers=a["auth"],
                               json={"username": "nguoi_giu_ten"})).status_code == 200


async def test_role_escalation_and_self_demotion_rules(client, db, mailer):
    a = await _admin(client, db, mailer)
    d = await _admin(client, db, mailer, "dash_quan_tri", role="dash_admin")
    v = await token_for(client, db, mailer, username="nguoi_thuong")
    # dash_admin không thăng ai (kể cả chính mình) lên admin
    for target in (v["id"], d["id"]):
        r = await client.patch(f"/v1/admin/users/{target}", headers=d["auth"], json={"role": "admin"})
        assert (r.status_code, r.json()["detail"]) == (403, "Chỉ quản trị viên (admin) mới được cấp quyền admin")
    # admin không tự hạ role
    r = await client.patch(f"/v1/admin/users/{a['id']}", headers=a["auth"], json={"role": "viewer"})
    assert (r.status_code, r.json()["detail"]) == (400, "Không thể hạ role chính mình")
    # admin thăng người khác lên admin được; role 'discord' không gán được qua API này
    assert (await client.patch(f"/v1/admin/users/{v['id']}", headers=a["auth"], json={"role": "admin"})).status_code == 200
    assert (await client.patch(f"/v1/admin/users/{v['id']}", headers=a["auth"], json={"role": "discord"})).status_code == 422


async def test_delete_user_removes_sessions_and_blocks_self_delete(client, db, mailer):
    a = await _admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="bi_xoa")
    r = await client.delete(f"/v1/admin/users/{a['id']}", headers=a["auth"])
    assert (r.status_code, r.json()["detail"]) == (400, "Không thể xoá chính mình")
    assert (await client.delete(f"/v1/admin/users/{u['id']}", headers=a["auth"])).json() == {"ok": True}
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": u["refresh"]})).status_code == 401
    assert (await login(client, "bi_xoa")).status_code == 401
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM refresh_tokens WHERE account_id=$1", u["id"]) == 0
    assert (await client.delete(f"/v1/admin/users/{u['id']}", headers=a["auth"])).status_code == 404


async def test_admin_actions_are_audited(client, db, mailer):
    a = await _admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="doi_tuong")
    key = (await client.post("/v1/admin/keys", headers=a["auth"], json={})).json()["key"]
    await client.delete(f"/v1/admin/keys/{key}", headers=a["auth"])
    await client.patch(f"/v1/admin/users/{u['id']}/status", headers=a["auth"], json={"status": "suspended"})
    await client.patch(f"/v1/admin/users/{u['id']}", headers=a["auth"], json={"role": "dash_admin"})
    await client.delete(f"/v1/admin/users/{u['id']}", headers=a["auth"])
    async with db.connection() as conn:
        events = [r["event_type"] for r in await conn.fetch(
            "SELECT event_type FROM auth_audit WHERE account_id=$1 ORDER BY id", a["id"])]
    assert events == ["register", "email_verified", "invite_key_created", "invite_key_revoked",
                      "account_status_changed", "account_updated", "account_deleted"]
