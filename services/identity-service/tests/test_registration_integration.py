"""Phase 1.7: đăng ký kèm Integration. Validate đồng bộ + cục bộ; sai thì KHÔNG tạo account, KHÔNG tiêu key; atomic."""

from __future__ import annotations

import json
import re
import uuid

import asyncpg
import pytest
from conftest import claims, login, make_key, token_for

pytestmark = pytest.mark.needs_pg

POLICY = {"allow_integration_registration": True, "default_client_kind": "primary_tool",
          "allowed_platforms": ["facebook", "youtube"], "allowed_scopes": ["integration:handshake", "resources:write",
                                                                           "snapshots:write", "metrics:write"],
          "max_integration_clients": 2, "contract_version": "2026-10-01"}
LEGACY_KEYS = {"ok", "email", "message"}


async def policy_key(db, policy=POLICY, *, apps=(), max_uses=3) -> str:
    key = await make_key(db, max_uses=max_uses)
    async with db.connection() as conn:
        await conn.execute("UPDATE invite_keys SET integration_policy=$2::jsonb, allowed_apps=$3 WHERE key=$1",
                           key, json.dumps(policy) if policy is not None else None, list(apps))
    return key


async def reg(client, key, username="nguoi_dung", integration=None, **extra):
    body = {"key": key, "username": username, "password": "mat-khau-12345", "email": f"{username}@example.com", **extra}
    if integration is not None:
        body["integration"] = integration
    return await client.post("/v1/auth/register", json=body,
                             headers={"X-Real-IP": f"172.20.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"})


async def snapshot(db, key) -> dict:
    async with db.connection() as conn:
        return {"accounts": await conn.fetchval("SELECT count(*) FROM accounts"),
                "used": await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key),
                "prov": await conn.fetchval("SELECT count(*) FROM integration_provisioning"),
                "outbox": await conn.fetchval("SELECT count(*) FROM outbox"),
                "perms": await conn.fetchval("SELECT count(*) FROM account_permissions")}


# ── đường hợp lệ ────────────────────────────────────────────────────────────────

async def test_valid_integration_registration_creates_account_provisioning_and_event_atomically(client, db, mailer):
    key = await policy_key(db)
    r = await reg(client, key, integration={"create": True, "tool_name": "Tool Cua Toi", "client_kind": "primary_tool",
                                            "platforms": ["facebook"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == LEGACY_KEYS | {"integration"}
    info = body["integration"]
    assert re.fullmatch(r"cli_[0-9a-f]{24}", info["client_id"])
    assert info == {"client_id": info["client_id"], "tool_name": "Tool Cua Toi", "client_kind": "primary_tool",
                    "credential_status": "pending_email_verification", "provisioning": "pending"}
    async with db.connection() as conn:
        prov = await conn.fetchrow("SELECT * FROM integration_provisioning")
        acc = await conn.fetchrow("SELECT id, status FROM accounts")
        ev = await conn.fetchrow("SELECT * FROM outbox WHERE type='account.registered'")
        used = await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key)
    assert (prov["client_id"], prov["account_id"], prov["status"], prov["tool_name"]) == (
        info["client_id"], acc["id"], "pending", "Tool Cua Toi")
    assert json.loads(prov["platforms"]) == ["facebook"]
    assert json.loads(prov["scopes"]) == ["integration:handshake", "resources:write", "snapshots:write", "metrics:write"]
    data = json.loads(ev["data"])
    assert data["provisioning"] == {"client_id": info["client_id"], "tool_name": "Tool Cua Toi",
                                    "client_kind": "primary_tool", "platforms": ["facebook"],
                                    "scopes": json.loads(prov["scopes"]), "contract_version": "2026-10-01"}
    assert data["account_id"] == acc["id"] and data["status"] == "pending" and used == 1
    assert "email" not in json.dumps(data) and "password" not in json.dumps(data)
    assert mailer.sent and mailer.sent[-1][0] == "verify"


async def test_defaults_come_from_policy_and_platforms_are_normalized(client, db):
    key = await policy_key(db)
    r = await reg(client, key, integration={"tool_name": "Tool", "platforms": ["Facebook", "facebook", "YOUTUBE"]})
    assert r.status_code == 200 and r.json()["integration"]["client_kind"] == "primary_tool"
    async with db.connection() as conn:
        assert json.loads(await conn.fetchval("SELECT platforms FROM integration_provisioning")) == ["facebook", "youtube"]


async def test_fallback_tool_gets_job_scopes_only_if_policy_allows(client, db):
    wide = {**POLICY, "allowed_scopes": [*POLICY["allowed_scopes"], "jobs:claim", "jobs:complete"]}
    r = await reg(client, await policy_key(db, wide), "fb_a", integration={"tool_name": "T", "client_kind": "fallback_tool"})
    assert r.status_code == 200
    async with db.connection() as conn:
        scopes = json.loads(await conn.fetchval("SELECT scopes FROM integration_provisioning WHERE tool_name='T'"))
    assert "jobs:claim" in scopes and "jobs:complete" in scopes and "jobs:claim:system" not in scopes
    r = await reg(client, await policy_key(db), "fb_b", integration={"tool_name": "T2", "client_kind": "fallback_tool"})
    assert r.status_code == 200                            # policy hẹp: cắt xuống giao với policy
    async with db.connection() as conn:
        scopes = json.loads(await conn.fetchval("SELECT scopes FROM integration_provisioning WHERE tool_name='T2'"))
    assert "jobs:claim" not in scopes and "integration:handshake" in scopes


# ── không yêu cầu => y hệt legacy ──────────────────────────────────────────────

async def test_registration_without_integration_is_identical_to_legacy_even_on_policy_key(client, db):
    key = await policy_key(db)
    r = await reg(client, key, "khong_integration")
    assert r.status_code == 200 and set(r.json()) == LEGACY_KEYS
    assert r.json()["message"] == "Đã gửi email xác thực. Kiểm tra hộp thư để kích hoạt tài khoản."
    plain = await reg(client, await make_key(db), "thuong")
    assert plain.json() == {**r.json(), "email": "thuong@example.com"}
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM integration_provisioning") == 0
        data = json.loads(await conn.fetchval("SELECT data FROM outbox WHERE type='account.registered' LIMIT 1"))
    assert "provisioning" not in data


async def test_create_false_means_no_integration(client, db):
    r = await reg(client, await policy_key(db), integration={"create": False, "tool_name": "T"})
    assert r.status_code == 200 and set(r.json()) == LEGACY_KEYS
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM integration_provisioning") == 0


# ── sai chính sách: lỗi đồng bộ, KHÔNG tác dụng phụ ────────────────────────────

@pytest.mark.parametrize("policy,integration,status,code", [
    (None, {"tool_name": "T"}, 403, "INTEGRATION_NOT_ALLOWED"),
    ({**POLICY, "allow_integration_registration": False}, {"tool_name": "T"}, 403, "INTEGRATION_NOT_ALLOWED"),
    (POLICY, {"tool_name": "T", "platforms": ["tiktok"]}, 403, "PLATFORM_NOT_ALLOWED"),
    (POLICY, {"tool_name": "T", "platforms": ["facebook", "tiktok"]}, 403, "PLATFORM_NOT_ALLOWED"),
    ({**POLICY, "allowed_platforms": []}, {"tool_name": "T", "platforms": ["facebook"]}, 403, "PLATFORM_NOT_ALLOWED"),
    (POLICY, {"tool_name": "T", "scopes": ["integration:handshake", "jobs:claim"]}, 403, "SCOPE_DENIED"),
    (POLICY, {"tool_name": "T", "scopes": ["jobs:claim:system"]}, 403, "SCOPE_DENIED"),
    ({**POLICY, "allowed_scopes": ["resources:write"]}, {"tool_name": "T"}, 403, "SCOPE_DENIED"),  # thiếu handshake
    ({**POLICY, "max_integration_clients": 0}, {"tool_name": "T"}, 403, "INTEGRATION_LIMIT_EXCEEDED"),
    (POLICY, {"tool_name": "T", "client_kind": "root"}, 422, "REQUEST_VALIDATION_FAILED"),
    (POLICY, {"tool_name": ""}, 422, "REQUEST_VALIDATION_FAILED"),
    (POLICY, {"tool_name": "<script>"}, 422, "REQUEST_VALIDATION_FAILED"),
    (POLICY, {"tool_name": "T", "platforms": ["Face book"]}, 422, "REQUEST_VALIDATION_FAILED"),
])
async def test_policy_violation_is_synchronous_and_leaves_no_trace(client, db, policy, integration, status, code):
    key = await policy_key(db, policy)
    before = await snapshot(db, key)
    r = await reg(client, key, integration=integration)
    assert r.status_code == status, r.text
    assert r.json()["code"] == code and "detail" in r.json()
    assert await snapshot(db, key) == before == {"accounts": 0, "used": 0, "prov": 0, "outbox": 0, "perms": 0}
    ok = await reg(client, key, "van_dung_duoc")                 # key chưa bị tiêu hao: đăng ký thường vẫn được
    assert ok.status_code == 200


async def test_unknown_field_in_integration_is_rejected_and_not_reflected(client, db):
    key = await policy_key(db)
    r = await reg(client, key, integration={"tool_name": "T", "owner_account_id": "ke-gia-mao-123"})
    assert r.status_code == 422 and "ke-gia-mao-123" not in r.text
    assert (await snapshot(db, key))["accounts"] == 0


async def test_single_use_key_survives_a_rejected_integration_request(client, db):
    key = await policy_key(db, max_uses=1)
    assert (await reg(client, key, integration={"tool_name": "T", "platforms": ["tiktok"]})).status_code == 403
    assert (await reg(client, key, integration={"tool_name": "T", "platforms": ["facebook"]})).status_code == 200
    assert (await reg(client, key, "nguoi_hai")).status_code == 400            # lượt duy nhất đã dùng


async def test_failure_after_account_creation_rolls_everything_back(client, db):
    """Lỗi THẬT của Postgres ở bước cuối (trigger chặn ghi provisioning) => không account, không key hao, không event."""
    key = await policy_key(db)
    async with db.connection() as conn:
        await conn.execute("CREATE OR REPLACE FUNCTION fail_prov() RETURNS trigger AS $$ BEGIN "
                           "RAISE EXCEPTION 'loi gia lap'; END $$ LANGUAGE plpgsql")
        await conn.execute("CREATE TRIGGER trg_fail_prov BEFORE INSERT ON integration_provisioning "
                           "FOR EACH ROW EXECUTE FUNCTION fail_prov()")
    try:
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await reg(client, key, integration={"tool_name": "T", "platforms": ["facebook"]})
    finally:
        async with db.connection() as conn:
            await conn.execute("DROP TRIGGER trg_fail_prov ON integration_provisioning")
            await conn.execute("DROP FUNCTION fail_prov()")
    assert await snapshot(db, key) == {"accounts": 0, "used": 0, "prov": 0, "outbox": 0, "perms": 0}


async def test_duplicate_username_with_integration_conflicts_without_provisioning(client, db):
    key = await policy_key(db)
    assert (await reg(client, key, integration={"tool_name": "T"})).status_code == 200
    again = await reg(client, key, integration={"tool_name": "T2"})
    assert again.status_code == 409
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM integration_provisioning") == 1
        assert await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key) == 1


# ── Admin tạo key: policy + allowed_apps ───────────────────────────────────────

async def test_admin_creates_key_with_policy_and_apps_and_it_is_listed_and_audited(client, db, mailer):
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    r = await client.post("/v1/admin/keys", headers=admin["auth"],
                          json={"role": "viewer", "label": "doi tac", "integration_policy": POLICY,
                                "allowed_apps": ["reward"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["integration_policy"] == POLICY and out["allowed_apps"] == ["reward"]
    listed = (await client.get("/v1/admin/keys", headers=admin["auth"])).json()["keys"]
    assert [k for k in listed if k["key"] == out["key"]][0]["integration_policy"] == POLICY
    plain = (await client.post("/v1/admin/keys", headers=admin["auth"], json={"role": "viewer"})).json()
    assert set(plain) == {"key", "role", "label", "max_uses", "used_count", "created_at", "expires_at", "status"}


@pytest.mark.parametrize("policy", [
    {"allow_integration_registration": True, "allowed_platforms": ["Face Book"]},
    {"allow_integration_registration": True, "default_client_kind": "root"},
    {"allow_integration_registration": True, "max_integration_clients": -1},
    {"allow_integration_registration": True, "max_integration_clients": True},
    {"allow_integration_registration": True, "allowed_scopes": ["khong hop le"]},
    {"allow_integration_registration": True, "contract_version": "latest"},
    {"allow_integration_registration": True, "tuy_y": 1},
    "khong-phai-object",
])
async def test_invalid_policy_is_rejected_when_creating_key(client, db, mailer, policy):
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    r = await client.post("/v1/admin/keys", headers=admin["auth"], json={"integration_policy": policy})
    assert r.status_code in (403, 422)


async def test_policy_cannot_grant_system_job_scope(client, db, mailer):
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    r = await client.post("/v1/admin/keys", headers=admin["auth"],
                          json={"integration_policy": {**POLICY, "allowed_scopes": ["jobs:claim:system"]}})
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_DENIED"


async def test_allowed_apps_must_exist_in_permission_catalog(client, db, mailer):
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    async with db.connection() as conn:
        before = await conn.fetchval("SELECT count(*) FROM invite_keys")
    for bad in (["khong_co_app"], ["reward", "ghost"], ["Bad App"], ["reward:access"]):
        r = await client.post("/v1/admin/keys", headers=admin["auth"], json={"allowed_apps": bad})
        assert r.status_code == 422, bad
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM invite_keys") == before


async def test_dash_admin_cannot_attach_admin_only_app_permission(client, db, mailer):
    async with db.connection() as conn:
        await conn.execute("INSERT INTO permissions (name, owner_service, admin_only) VALUES ('vault:access','x',true)")
    dash = await token_for(client, db, mailer, username="dash", role="dash_admin")
    assert (await client.post("/v1/admin/keys", headers=dash["auth"], json={"allowed_apps": ["vault"]})).status_code == 403
    admin = await token_for(client, db, mailer, username="quan_tri", role="admin")
    assert (await client.post("/v1/admin/keys", headers=admin["auth"], json={"allowed_apps": ["vault"]})).status_code == 200


# ── allowed_apps: SSO cấp permission <app>:access ──────────────────────────────

async def test_registration_with_allowed_apps_grants_app_access_permission(client, db, mailer):
    key = await policy_key(db, None, apps=["reward"])
    assert (await reg(client, key, "co_quyen")).status_code == 200
    async with db.connection() as conn:
        types = [r["type"] for r in await conn.fetch("SELECT type FROM outbox ORDER BY seq")]
    # Phase 2: ngoài reward:access còn cấp 2 capability mặc định của viewer → 3 permission_changed
    assert types[0] == "account.registered" and len(types) == 4
    assert types.count("account.permission_changed") == 3
    await client.get("/v1/auth/verify-email", params={"token": mailer.last("verify")})
    token = (await login(client, "co_quyen")).json()["access_token"]
    assert "reward:access" in (await claims(client, token))["perms"]
    async with db.connection() as conn:
        row = await conn.fetchrow(
            "SELECT permission, granted_by FROM account_permissions WHERE permission='reward:access'")
        assert (row["permission"], row["granted_by"]) == ("reward:access", "registration")


async def test_key_without_apps_grants_nothing_and_other_apps_stay_closed(client, db, mailer):
    async with db.connection() as conn:
        await conn.execute("INSERT INTO permissions (name, owner_service) VALUES ('site_b:access','x')")
    key = await policy_key(db, None, apps=["site_b"])
    await reg(client, key, "chi_site_b")
    async with db.connection() as conn:
        # site_b:access cấp đúng 1 lần (cho chi_site_b), Phase 2 cũng cấp default caps
        site_b_grants = await conn.fetchval(
            "SELECT count(*) FROM account_permissions WHERE permission='site_b:access'")
        assert site_b_grants == 1
    await reg(client, await make_key(db), "khong_app")
    async with db.connection() as conn:
        # khong_app không có site_b:access — chỉ có default caps
        khong_id = await conn.fetchval("SELECT id FROM accounts WHERE username='khong_app'")
        no_site_b = await conn.fetchval(
            "SELECT count(*) FROM account_permissions WHERE account_id=$1 AND permission='site_b:access'",
            khong_id)
        assert no_site_b == 0


async def test_app_removed_from_catalog_after_key_creation_rejects_registration_without_consuming(client, db):
    key = await policy_key(db, None, apps=["reward"])
    async with db.connection() as conn:
        saved = dict(await conn.fetchrow("SELECT * FROM permissions WHERE name='reward:access'"))
        await conn.execute("DELETE FROM permissions WHERE name='reward:access'")
    try:
        r = await reg(client, key)
        assert r.status_code == 400
        assert (await snapshot(db, key))["accounts"] == 0 and (await snapshot(db, key))["used"] == 0
    finally:  # bảng catalog không bị TRUNCATE giữa các test: PHẢI khôi phục
        async with db.connection() as conn:
            await conn.execute("INSERT INTO permissions (name, description, owner_service, admin_only, created_at) "
                               "VALUES ($1,$2,$3,$4,$5)", saved["name"], saved["description"], saved["owner_service"],
                               saved["admin_only"], saved["created_at"])


async def test_integration_and_apps_work_together(client, db, mailer):
    key = await policy_key(db, apps=["reward"])
    r = await reg(client, key, "ca_hai", integration={"tool_name": "T", "platforms": ["youtube"]})
    assert r.status_code == 200
    s = await snapshot(db, key)
    # Phase 2: perms = reward:access + 2 default viewer caps; outbox = registered + 3×permission_changed
    assert (s["accounts"], s["prov"], s["perms"], s["outbox"]) == (1, 1, 3, 4)


# ── GET /v1/accounts/me/integration ────────────────────────────────────────────

async def test_me_integration_reports_status_transitions(client, db, mailer):
    key = await policy_key(db)
    cid = (await reg(client, key, "co_tool", integration={"tool_name": "T"})).json()["integration"]["client_id"]
    await client.get("/v1/auth/verify-email", params={"token": mailer.last("verify")})
    auth = {"Authorization": f"Bearer {(await login(client, 'co_tool')).json()['access_token']}"}
    r = await client.get("/v1/accounts/me/integration", headers=auth)
    assert r.status_code == 200 and r.json() == {"client_id": cid, "status": "pending"}
    async with db.connection() as conn:
        await conn.execute("UPDATE integration_provisioning SET status='provisioned'")
    assert (await client.get("/v1/accounts/me/integration", headers=auth)).json() == {"client_id": cid,
                                                                                     "status": "provisioned"}
    async with db.connection() as conn:
        await conn.execute("UPDATE integration_provisioning SET status='failed', error_code='PLATFORM_NOT_ALLOWED'")
    assert (await client.get("/v1/accounts/me/integration", headers=auth)).json() == {
        "client_id": cid, "status": "failed", "error_code": "PLATFORM_NOT_ALLOWED"}


async def test_me_integration_requires_auth_and_is_per_account(client, db, mailer):
    assert (await client.get("/v1/accounts/me/integration")).status_code == 401
    other = await token_for(client, db, mailer, username="khong_tool")
    r = await client.get("/v1/accounts/me/integration", headers=other["auth"])
    assert r.status_code == 404
    key = await policy_key(db)
    await reg(client, key, "co_tool", integration={"tool_name": "T"})
    assert (await client.get("/v1/accounts/me/integration", headers=other["auth"])).status_code == 404   # không thấy của người khác
