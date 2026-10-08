"""Phase 2: Capability model (docs/architecture/capability-model-v1.md). Capability = permission `capability:*`,
tái dùng `permissions`/`account_permissions`. Test: default theo role lúc đăng ký, `capability_grant` trên
invite key (cộng thêm / override), JWT `perms` phản ánh capability, và API admin grant/revoke/list.
"""

from __future__ import annotations

import json

import pytest
from conftest import claims, login, make_key, register, token_for

pytestmark = pytest.mark.needs_pg

ALL_CAPS = {"capability:web_access", "capability:dashboard_access", "capability:manual_channel_mgmt",
            "capability:tool_integration", "capability:api_write", "capability:api_read",
            "capability:multi_tool"}


async def admin(client, db, mailer, name="quan_tri"):
    return await token_for(client, db, mailer, username=name, role="admin")


async def granted_caps(db, account_id: str) -> set[str]:
    async with db.connection() as conn:
        rows = await conn.fetch("SELECT permission FROM account_permissions WHERE account_id = $1", account_id)
    return {r["permission"] for r in rows}


async def account_id(db, username: str) -> str:
    async with db.connection() as conn:
        return await conn.fetchval("SELECT id FROM accounts WHERE username = $1", username)


async def capability_key(db, *, role: str = "viewer", capability_grant: dict | None = None,
                         max_uses: int = 3) -> str:
    key = await make_key(db, role=role, max_uses=max_uses)
    async with db.connection() as conn:
        await conn.execute("UPDATE invite_keys SET capability_grant=$2::jsonb WHERE key=$1", key,
                           json.dumps(capability_grant) if capability_grant is not None else None)
    return key


async def register_verified_with_key(client, db, mailer, *, username: str, key: str, role: str = "viewer"):
    r = await register(client, db, username=username, role=role, key=key)
    assert r.status_code == 200, r.text
    v = await client.get("/v1/auth/verify-email", params={"token": mailer.last("verify")})
    assert v.status_code == 200, v.text


# ── Catalog ─────────────────────────────────────────────────────────────────────

async def test_catalog_is_seeded_with_capability_permissions(client, db, mailer):
    a = await admin(client, db, mailer)
    names = {p["name"]: p for p in
             (await client.get("/v1/admin/permissions", headers=a["auth"])).json()["permissions"]}
    for cap in ALL_CAPS:
        assert names[cap]["admin_only"] is False


# ── Default theo role lúc đăng ký ────────────────────────────────────────────────

@pytest.mark.parametrize("role,expected", [
    ("viewer", {"capability:web_access", "capability:dashboard_access"}),
    ("dash_admin", {"capability:web_access", "capability:dashboard_access"}),
    ("admin", ALL_CAPS),
    ("discord", set()),
])
async def test_registration_grants_default_capabilities_by_role(client, db, mailer, role, expected):
    key = await capability_key(db, role=role)
    await register_verified_with_key(client, db, mailer, username=f"mac_dinh_{role}", key=key, role=role)
    aid = await account_id(db, f"mac_dinh_{role}")
    assert expected <= await granted_caps(db, aid)


# ── `capability_grant` trên invite key ───────────────────────────────────────────

async def test_capability_grant_adds_to_role_defaults_when_not_overriding(client, db, mailer):
    key = await capability_key(db, role="viewer",
                               capability_grant={"capabilities": ["capability:tool_integration"],
                                                  "override_defaults": False})
    await register_verified_with_key(client, db, mailer, username="them_quyen", key=key, role="viewer")
    aid = await account_id(db, "them_quyen")
    assert await granted_caps(db, aid) == {"capability:web_access", "capability:dashboard_access",
                                           "capability:tool_integration"}


async def test_capability_grant_overrides_role_defaults_when_requested(client, db, mailer):
    key = await capability_key(db, role="viewer",
                               capability_grant={"capabilities": ["capability:tool_integration"],
                                                  "override_defaults": True})
    await register_verified_with_key(client, db, mailer, username="thay_the", key=key, role="viewer")
    aid = await account_id(db, "thay_the")
    assert await granted_caps(db, aid) == {"capability:tool_integration"}


async def test_jwt_perms_reflect_granted_capabilities_after_login(client, db, mailer):
    key = await capability_key(db, role="viewer",
                               capability_grant={"capabilities": ["capability:tool_integration"],
                                                  "override_defaults": False})
    await register_verified_with_key(client, db, mailer, username="jwt_capability", key=key, role="viewer")
    token = (await login(client, "jwt_capability")).json()["access_token"]
    c = await claims(client, token)
    assert {"capability:web_access", "capability:dashboard_access", "capability:tool_integration"} <= set(c["perms"])


# ── Admin tạo key: capability_grant được chuẩn hoá + trả lại nguyên vẹn ──────────

async def test_admin_created_key_echoes_capability_grant(client, db, mailer):
    a = await admin(client, db, mailer)
    grant = {"capabilities": ["capability:tool_integration", "capability:api_write"], "override_defaults": False}
    r = await client.post("/v1/admin/keys", headers=a["auth"], json={"capability_grant": grant})
    assert r.status_code == 200, r.text
    assert r.json()["capability_grant"] == grant


async def test_admin_key_capability_grant_must_exist_in_catalog(client, db, mailer):
    a = await admin(client, db, mailer)
    before = await client.get("/v1/admin/keys", headers=a["auth"])
    before_count = len(before.json()["keys"])
    r = await client.post("/v1/admin/keys", headers=a["auth"],
                          json={"capability_grant": {"capabilities": ["capability:does_not_exist"]}})
    assert r.status_code == 422
    after = await client.get("/v1/admin/keys", headers=a["auth"])
    assert len(after.json()["keys"]) == before_count


# ── API admin: grant / revoke / list capability ──────────────────────────────────

async def test_admin_can_grant_list_and_revoke_a_capability(client, db, mailer):
    a = await admin(client, db, mailer)
    key = await capability_key(db, role="viewer")
    await register_verified_with_key(client, db, mailer, username="muc_tieu", key=key, role="viewer")
    uid = await account_id(db, "muc_tieu")

    r = await client.post(f"/v1/admin/accounts/{uid}/capabilities", headers=a["auth"],
                          json={"capabilities": ["capability:tool_integration"]})
    assert (r.status_code, r.json()["granted"]) == (200, ["capability:tool_integration"])

    listed = await client.get(f"/v1/admin/accounts/{uid}/capabilities", headers=a["auth"])
    assert set(listed.json()["capabilities"]) == {"capability:web_access", "capability:dashboard_access",
                                                  "capability:tool_integration"}

    revoked = await client.delete(f"/v1/admin/accounts/{uid}/capabilities/capability:tool_integration",
                                  headers=a["auth"])
    assert (revoked.status_code, revoked.json()["changed"]) == (200, True)
    assert "capability:tool_integration" not in await granted_caps(db, uid)


async def test_capability_endpoints_reject_non_capability_permission_names(client, db, mailer):
    a = await admin(client, db, mailer)
    key = await capability_key(db, role="viewer")
    await register_verified_with_key(client, db, mailer, username="ten_sai", key=key, role="viewer")
    uid = await account_id(db, "ten_sai")
    r = await client.post(f"/v1/admin/accounts/{uid}/capabilities", headers=a["auth"],
                          json={"capabilities": ["reward:access"]})
    assert r.status_code == 422
    d = await client.delete(f"/v1/admin/accounts/{uid}/capabilities/reward:access", headers=a["auth"])
    assert d.status_code == 422


async def test_capability_endpoints_are_admin_only_not_dash_admin(client, db, mailer):
    d = await token_for(client, db, mailer, username="dash_quan_tri", role="dash_admin")
    key = await capability_key(db, role="viewer")
    await register_verified_with_key(client, db, mailer, username="muc_tieu_2", key=key, role="viewer")
    uid = await account_id(db, "muc_tieu_2")
    assert (await client.get(f"/v1/admin/accounts/{uid}/capabilities", headers=d["auth"])).status_code == 403
    assert (await client.post(f"/v1/admin/accounts/{uid}/capabilities", headers=d["auth"],
                              json={"capabilities": ["capability:tool_integration"]})).status_code == 403
    assert (await client.delete(f"/v1/admin/accounts/{uid}/capabilities/capability:web_access",
                                headers=d["auth"])).status_code == 403
