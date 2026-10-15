"""Client + credential (mặt phẳng USER): wizard client dùng chung (admin), tự provision client cá nhân (capability),
cấp/xoay/thu hồi API key, reveal, điều kiện email/account, phân quyền admin/dash_admin/chủ."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import ISSUER, AUDIENCE
from conftest import ts  # noqa: F401
from integration_service.security import API_KEY_RE, sha256_hex

pytestmark = pytest.mark.needs_pg
BASE = "/v1/integrations"


async def outbox_types(env) -> list[str]:
    return [r["type"] for r in await env.rows("SELECT type FROM outbox ORDER BY seq")]


async def create_shared(env, admin, **over):
    """Bước 1 wizard dùng chung (CHỈ admin)."""
    body = {"tool_name": "T", "client_kind": "fallback_tool", "purpose": "test fixture",
            "platforms": ["facebook"], **over}
    return await env.http.post(f"{BASE}/clients", headers=admin["headers"], json=body)


async def configure(env, admin, client_id: str, **over):
    body = {"allowed_platforms": ["facebook"], "allowed_scopes": ["integration:handshake", "resources:write",
            "snapshots:write", "metrics:write", "jobs:claim", "jobs:complete"], **over}
    return await env.http.put(f"{BASE}/clients/{client_id}/config", headers=admin["headers"], json=body)


async def activate(env, admin, client_id: str, tool_name: str = "T"):
    return await env.http.post(f"{BASE}/clients/{client_id}/activate", headers=admin["headers"],
                               json={"confirm_tool_name": tool_name})


async def whoami(tool):
    return await tool.post("/v1/integrations/handshake", {"installation": {"device_id": tool.device_id}})


# ── Wizard client dùng chung (CHỈ admin) ─────────────────────────────────────────

async def test_shared_wizard_requires_super_admin_and_rejects_primary_tool(env):
    u = await env.verified_user()
    dash = await env.verified_user(role="dash_admin")
    admin = await env.verified_user(role="admin")
    assert (await create_shared(env, u)).status_code == 403
    r = await create_shared(env, dash)
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"
    r = await create_shared(env, admin, client_kind="primary_tool")
    assert r.status_code == 422
    r = await create_shared(env, admin)
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["status"] == "draft" and data["ownership"] == "shared" and data["client_kind"] == "fallback_tool"
    assert data["purpose"] == "test fixture" and data["owner_account_id"] == admin["sub"]


async def test_wizard_body_rejects_unknown_fields_bad_purpose_and_bad_kind(env):
    admin = await env.verified_user(role="admin")
    assert (await create_shared(env, admin, owner_account_id="x")).status_code == 422
    assert (await create_shared(env, admin, purpose="")).status_code == 422
    assert (await create_shared(env, admin, client_kind="root")).status_code == 422


async def test_configure_clamps_platforms_and_scopes_follow_client_kind(env):
    admin = await env.verified_user(role="admin")
    cid = (await create_shared(env, admin, client_kind="service")).json()["data"]["client_id"]
    r = await configure(env, admin, cid, allowed_platforms=["khong_co"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    r = await configure(env, admin, cid, allowed_platforms=[])
    assert r.status_code == 422
    r = await configure(env, admin, cid, allowed_scopes=["jobs:claim"])   # service không được scope fallback
    assert r.status_code == 422
    r = await configure(env, admin, cid, allowed_scopes=["jobs:claim:system"])
    assert r.status_code == 422                                          # system scope chỉ fallback_tool
    r = await configure(env, admin, cid, allowed_scopes=["resources:write"])
    assert r.status_code == 200 and r.json()["data"]["status"] == "draft" and r.json()["data"]["configured_at"]

    cid2 = (await create_shared(env, admin, client_kind="fallback_tool")).json()["data"]["client_id"]
    r = await configure(env, admin, cid2, allowed_scopes=["jobs:claim", "jobs:claim:system"])
    assert r.status_code == 200 and set(r.json()["data"]["allowed_scopes"]) == {"jobs:claim", "jobs:claim:system"}
    # Chỉ cấu hình được khi còn draft
    await activate(env, admin, cid2)
    assert (await configure(env, admin, cid2)).status_code == 409


async def test_activate_requires_configured_and_matching_confirm_name(env):
    admin = await env.verified_user(role="admin")
    cid = (await create_shared(env, admin)).json()["data"]["client_id"]
    r = await activate(env, admin, cid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CLIENT_NOT_READY"
    await configure(env, admin, cid)
    r = await activate(env, admin, cid, tool_name="Sai ten")
    assert r.status_code == 422
    r = await activate(env, admin, cid)
    assert r.status_code == 200 and r.json()["data"]["status"] == "active"
    assert r.json()["data"]["activated_at"] and r.json()["data"]["activated_by"] == admin["sub"]
    assert (await activate(env, admin, cid)).status_code == 409   # đã active, không active lại được


async def test_draft_client_cannot_issue_credential_or_be_patched_to_active(env):
    admin = await env.verified_user(role="admin")
    cid = (await create_shared(env, admin)).json()["data"]["client_id"]
    r = await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=admin["headers"], json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CLIENT_NOT_READY"
    r = await env.http.patch(f"{BASE}/clients/{cid}", headers=admin["headers"], json={"status": "active"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CLIENT_NOT_READY"


# ── Tự provision client CÁ NHÂN (capability:tool_integration) ───────────────────

async def test_capability_grant_creates_active_or_pending_personal_client(env):
    verified = await env.verified_user()
    await env.grant_capability(verified["sub"])
    row = (await env.rows("SELECT * FROM integration_clients WHERE owner_account_id = $1", verified["sub"]))[0]
    assert row["status"] == "active" and row["ownership"] == "personal" and row["client_kind"] == "primary_tool"
    cred = (await env.rows("SELECT reveal_state FROM integration_credentials WHERE client_id = $1", row["id"]))[0]
    assert cred["reveal_state"] == "pending"

    unverified = env.user()   # không có account_projection (chưa xác thực)
    await env.grant_capability(unverified["sub"])
    row2 = (await env.rows("SELECT status FROM integration_clients WHERE owner_account_id = $1", unverified["sub"]))[0]
    assert row2["status"] == "pending_email_verification"


async def test_reveal_requires_capability_and_existing_personal_client(env):
    no_cap = await env.verified_user(perms=[])
    r = await env.http.post(f"{BASE}/me/credential/reveal", headers=no_cap["headers"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"

    has_cap_not_provisioned = await env.verified_user()   # claim có capability nhưng chưa có sự kiện provisioning thật
    r = await env.http.post(f"{BASE}/me/credential/reveal", headers=has_cap_not_provisioned["headers"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "CLIENT_NOT_FOUND"


async def test_reveal_shows_plaintext_once_then_404_and_expired_credential_gives_410(env, clock):
    u = await env.verified_user()
    await env.grant_capability(u["sub"])
    r = await env.http.post(f"{BASE}/me/credential/reveal", headers=u["headers"])
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["api_key"].startswith("mpk_")
    key_id = data["key_id"]
    assert await env.scalar("SELECT reveal_ciphertext FROM integration_credentials WHERE key_id = $1", key_id) is None
    assert await env.scalar("SELECT reveal_state FROM integration_credentials WHERE key_id = $1", key_id) == "revealed"
    r2 = await env.http.post(f"{BASE}/me/credential/reveal", headers=u["headers"])
    assert r2.status_code == 404 and r2.json()["error"]["code"] == "REVEAL_NOT_AVAILABLE"

    # Credential pending khác hết hạn => 410, ciphertext bị xoá
    u2 = await env.verified_user()
    await env.grant_capability(u2["sub"])
    clock.advance(days=8)   # mặc định CREDENTIAL_REVEAL_TTL_DAYS=7
    r3 = await env.http.post(f"{BASE}/me/credential/reveal", headers=u2["headers"])
    assert r3.status_code == 410 and r3.json()["error"]["code"] == "REVEAL_EXPIRED"
    key_id2 = await env.scalar("SELECT key_id FROM integration_credentials WHERE client_id = (SELECT id FROM "
                               "integration_clients WHERE owner_account_id = $1)", u2["sub"])
    assert await env.scalar("SELECT reveal_state FROM integration_credentials WHERE key_id = $1", key_id2) == "expired"
    assert await env.scalar("SELECT reveal_ciphertext FROM integration_credentials WHERE key_id = $1", key_id2) is None


async def test_reveal_blocked_until_email_verified_then_works_after_verified_event(env):
    u = env.user()   # chưa xác thực email -> không có projection
    await env.grant_capability(u["sub"])
    r = await env.http.post(f"{BASE}/me/credential/reveal", headers=u["headers"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    await env.projection(u["sub"], verified=True, status="active")
    container = env.app.state.container
    from integration_service.provisioning import handlers
    async with env.db.transaction() as conn:
        await handlers.apply_event(conn, container.clients, container.credentials,
                                   {"type": "account.verified", "data": {"account_id": u["sub"]}}, env.clock())
    assert await env.scalar("SELECT status FROM integration_clients WHERE owner_account_id = $1", u["sub"]) == "active"
    r2 = await env.http.post(f"{BASE}/me/credential/reveal", headers=u["headers"])
    assert r2.status_code == 200 and r2.json()["data"]["api_key"].startswith("mpk_")


# ── Xem / phân quyền ────────────────────────────────────────────────────────────

async def test_list_and_get_visibility_with_ownership_purpose_and_credentials(env):
    a, b = await env.verified_user(), await env.verified_user()
    await env.grant_capability(a["sub"])
    admin = await env.verified_user(role="admin")
    cid_a = (await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", a["sub"]))[0]["id"]

    r = await env.http.get(f"{BASE}/clients", headers=a["headers"])
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["client_id"] == cid_a
    assert items[0]["ownership"] == "personal" and "purpose" in items[0] and "credentials" in items[0]
    assert items[0]["credentials"][0]["key_id"] and items[0]["credentials"][0]["reveal_state"] == "pending"

    assert (await env.http.get(f"{BASE}/clients", headers=b["headers"])).json()["data"]["items"] == []
    r_admin = await env.http.get(f"{BASE}/clients", headers=admin["headers"])
    admin_items = r_admin.json()["data"]["items"]
    assert any(it["client_id"] == cid_a for it in admin_items)

    assert (await env.http.get(f"{BASE}/clients/{cid_a}", headers=b["headers"])).status_code == 404
    detail = (await env.http.get(f"{BASE}/clients/{cid_a}", headers=a["headers"])).json()["data"]
    assert detail["ownership"] == "personal" and "credentials" in detail and "installations" in detail
    assert (await env.http.get(f"{BASE}/clients/cli_khongco", headers=a["headers"])).status_code == 404


async def test_client_detail_never_exposes_secret_or_hash(env):
    tool = await env.tool()
    r = await env.http.get(f"{BASE}/clients/{tool.client_id}", headers=env.user(sub=tool.owner)["headers"])
    text = r.text
    secret = tool.api_key.split(".")[1]
    assert tool.api_key not in text and "secret" not in text and "hash" not in text and secret not in text


# ── Cấp credential (owner / admin) ───────────────────────────────────────────────

async def test_issue_shows_plaintext_once_and_stores_only_hash(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"], json={})
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["api_key"].startswith("mpk_") and data["key_id"] != tool.key_id
    secret = data["api_key"].split(".")[1]
    for table in ("integration_credentials",):
        dump = str(await env.rows(f"SELECT * FROM {table}"))
        assert secret not in dump and tool.api_key.split(".")[1] not in dump, table
    row = (await env.rows("SELECT secret_hash FROM integration_credentials WHERE key_id = $1", data["key_id"]))[0]
    assert row["secret_hash"] == sha256_hex(secret)


async def test_admin_can_issue_for_any_client_but_stranger_cannot(env):
    """Admin có thể cấp credential cho client của bất kỳ user nào. Trên client personal, admin KHÔNG thấy plaintext
    — response có `reveal_state=pending`, chủ phải tự `/me/credential/reveal`."""
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=admin["headers"], json={})
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["api_key"] is None and data["reveal_state"] == "pending"
    owner_reveal = await env.http.post(f"{BASE}/me/credential/reveal", headers=env.user(sub=tool.owner)["headers"])
    assert owner_reveal.status_code == 200 and owner_reveal.json()["data"]["key_id"] == data["key_id"]

    stranger = await env.verified_user()
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=stranger["headers"], json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "CLIENT_NOT_FOUND"


async def test_dash_admin_can_view_but_not_rotate_or_revoke_other_owners_credentials(env):
    """`dash_admin` chỉ XEM (is_admin gồm cả hai role). Xoay/thu hồi credential => chỉ chủ client hoặc role `admin`."""
    tool = await env.tool()
    dash = await env.verified_user(role="dash_admin")
    assert (await env.http.get(f"{BASE}/clients/{tool.client_id}", headers=dash["headers"])).status_code == 200
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate",
                            headers=dash["headers"], json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}", headers=dash["headers"])
    assert r.status_code == 403
    assert await env.scalar("SELECT count(*) FROM integration_credentials WHERE client_id = $1", tool.client_id) == 1


async def test_admin_and_owner_can_rotate_and_revoke(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    owner = env.user(sub=tool.owner)
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate",
                            headers=admin["headers"], json={})
    assert r.status_code == 201
    new = r.json()["data"]
    assert new["api_key"] is None and new["reveal_state"] == "pending"   # admin xoay trên personal: chủ tự reveal
    revealed = await env.http.post(f"{BASE}/me/credential/reveal", headers=owner["headers"])
    k2 = revealed.json()["data"]["key_id"]
    assert k2 == new["key_id"]
    r2 = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{k2}", headers=owner["headers"])
    assert r2.status_code == 200


async def test_requested_scopes_cannot_exceed_client_scopes(env):
    u = await env.verified_user()
    await env.grant_capability(u["sub"])
    cid = (await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", u["sub"]))[0]["id"]
    r = await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"],
                            json={"scopes": ["jobs:claim:system"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"
    ok = await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"],
                             json={"scopes": ["snapshots:write"]})
    assert ok.status_code == 201 and ok.json()["data"]["scopes"] == ["snapshots:write"]
    assert ok.headers["cache-control"] == "no-store"


async def test_legacy_alias_path_issues_credentials(env):
    u = await env.verified_user()
    await env.grant_capability(u["sub"])
    cid = (await env.rows("SELECT id FROM integration_clients WHERE owner_account_id = $1", u["sub"]))[0]["id"]
    r = await env.http.post(f"/v1/integrations/{cid}/credentials", headers=u["headers"], json={})
    assert r.status_code == 201 and r.json()["data"]["api_key"].startswith("mpk_")


async def test_credential_count_limit(make_env):
    env, _ = await make_env(max_credentials_per_client=2)
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    r1 = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"], json={})
    assert r1.status_code == 201   # tool.key_id + r1 = 2 (đã đạt hạn mức)
    r2 = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"], json={})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "LIMIT_EXCEEDED"


# ── Dùng key: hết hạn / thu hồi / xoay ──────────────────────────────────────────

async def test_expired_key_is_rejected_with_auth_invalid(env, clock):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"],
                            json={"expires_in_days": 1})
    data = r.json()["data"]
    clock.advance(days=2)
    bad = await tool.http.post("/v1/integrations/handshake",
                               headers={"Authorization": f"Bearer {data['api_key']}"},
                               json={"installation": {"device_id": "x"}})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "AUTH_INVALID"


async def test_revoke_takes_effect_immediately_and_is_idempotent(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    assert (await whoami(tool)).status_code == 200
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}", headers=owner["headers"])
    assert r.status_code == 200 and r.json()["data"]["already_revoked"] is False
    again = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}", headers=owner["headers"])
    assert again.status_code == 200 and again.json()["data"]["already_revoked"] is True
    bad = await whoami(tool)
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "AUTH_REVOKED"
    ev = (await env.rows("SELECT data FROM outbox WHERE type = 'credential.revoked' ORDER BY seq"))[-1]["data"]
    assert ev == {"client_id": tool.client_id, "key_id": tool.key_id}


async def test_revoke_unknown_or_foreign_key(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/cred_khongco", headers=owner["headers"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


async def test_rotate_with_grace_keeps_old_key_until_grace_ends(env, clock):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate",
                            headers=owner["headers"], json={"grace_seconds": 300})
    assert r.status_code == 201
    new = r.json()["data"]
    assert new["key_id"] != tool.key_id and new["api_key"] != tool.api_key and new["previous_key_id"] == tool.key_id
    assert new["scopes"] == tool.scopes
    from conftest import Tool
    fresh = Tool(env.http, tool.client_id, tool.owner, new["key_id"], new["api_key"], new["scopes"],
                device_id="device-fresh")
    assert (await whoami(fresh)).status_code == 200   # credential mới chưa bind: handshake đầu tiên luôn thành công
    assert (await whoami(tool)).status_code == 200     # key cũ còn hiệu lực trong grace


async def test_rotate_without_grace_revokes_old_key_immediately(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate",
                            headers=owner["headers"], json={})
    assert r.status_code == 201
    r2 = await whoami(tool)
    assert r2.status_code == 401 and r2.json()["error"]["code"] == "AUTH_REVOKED"


async def test_last_used_at_is_recorded_and_throttled(env, clock):
    tool = await env.tool(handshake=False)
    assert await env.scalar("SELECT last_used_at FROM integration_credentials WHERE key_id = $1", tool.key_id) is None
    r = await tool.post("/v1/integrations/handshake", {"installation": {"device_id": "d1"}})
    assert r.status_code == 200
    first = await env.scalar("SELECT last_used_at FROM integration_credentials WHERE key_id = $1", tool.key_id)
    assert first is not None
    tool.installation_id = r.json()["data"]["installation_id"]
    tool.device_id = "d1"
    clock.advance(minutes=2)   # touch_last_used điều tiết nghiêm ngặt > 60s (không phải >=)
    assert (await whoami(tool)).status_code == 200
    assert await env.scalar("SELECT last_used_at FROM integration_credentials WHERE key_id = $1", tool.key_id) > first


# ── Admin ───────────────────────────────────────────────────────────────────────

async def test_patch_client_is_admin_only(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    assert (await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=owner["headers"],
                                 json={"status": "suspended"})).status_code == 403   # chủ cũng không tự gỡ giới hạn


async def test_wizard_client_kind_scope_policy_enforced_at_configure(env):
    admin = await env.verified_user(role="admin")
    cid = (await create_shared(env, admin, client_kind="service")).json()["data"]["client_id"]
    r = await configure(env, admin, cid, allowed_scopes=["jobs:claim:system"])
    assert r.status_code == 422
    dash = await env.verified_user(role="dash_admin")
    r2 = await create_shared(env, dash)
    assert r2.status_code == 403
    r3 = await env.http.patch(f"{BASE}/clients/{cid}", headers=admin["headers"], json={"owner_account_id": "x"})
    assert r3.status_code == 422


async def test_narrowing_client_scopes_applies_to_existing_keys_at_once(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    r = await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"],
                             json={"allowed_scopes": ["integration:handshake"]})
    assert r.status_code == 200
    r2 = await tool.post("/v1/integrations/resources/batch", {"items": []})
    assert r2.status_code == 403 and r2.json()["error"]["code"] == "SCOPE_DENIED"


async def test_suspend_revokes_every_credential_and_blocks_new_ones(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    owner = env.user(sub=tool.owner)
    r = await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"], json={"status": "suspended"})
    assert r.status_code == 200
    assert await env.scalar("SELECT count(*) FROM integration_credentials WHERE status = 'revoked'") == 1
    assert (await whoami(tool)).status_code == 401
    blocked = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"], json={})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "CLIENT_NOT_ACTIVE"
    r2 = await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"], json={"status": "active"})
    assert r2.status_code == 200
    assert (await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"],
                                json={})).status_code == 201


async def test_admin_cannot_activate_client_whose_owner_email_is_not_verified(env):
    admin = await env.verified_user(role="admin")
    cid = (await create_shared(env, admin)).json()["data"]["client_id"]
    await configure(env, admin, cid)
    await activate(env, admin, cid)
    await env.http.patch(f"{BASE}/clients/{cid}", headers=admin["headers"], json={"status": "suspended"})
    await env.projection(admin["sub"], verified=False, status="active")
    r = await env.http.patch(f"{BASE}/clients/{cid}", headers=admin["headers"], json={"status": "active"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


async def test_deleted_client_stays_deleted(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"], json={"status": "deleted"})
    r = await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"], json={"status": "active"})
    assert r.status_code == 409


# ── Chuyển chủ client personal ───────────────────────────────────────────────────

async def test_transfer_personal_client_rules(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    target_no_account = {"new_owner_account_id": "khong_ton_tai"}
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/transfer", headers=admin["headers"], json=target_no_account)
    assert r.status_code == 422

    other_personal = await env.tool()   # đã có client personal active -> không nhận chuyển
    r2 = await env.http.post(f"{BASE}/clients/{tool.client_id}/transfer", headers=admin["headers"],
                             json={"new_owner_account_id": other_personal.owner})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "PERSONAL_CLIENT_EXISTS"

    new_owner = await env.verified_user()
    r3 = await env.http.post(f"{BASE}/clients/{tool.client_id}/transfer", headers=admin["headers"],
                             json={"new_owner_account_id": new_owner["sub"]})
    assert r3.status_code == 200
    assert r3.json()["data"]["owner_account_id"] == new_owner["sub"]
    assert await env.scalar("SELECT status FROM integration_credentials WHERE key_id = $1", tool.key_id) == "revoked"
    assert (await whoami(tool)).status_code == 401
    reveal = await env.http.post(f"{BASE}/me/credential/reveal", headers=new_owner["headers"])
    assert reveal.status_code == 200 and reveal.json()["data"]["client_id"] == tool.client_id

    shared = (await create_shared(env, admin)).json()["data"]["client_id"]
    r4 = await env.http.post(f"{BASE}/clients/{shared}/transfer", headers=admin["headers"],
                             json={"new_owner_account_id": new_owner["sub"]})
    assert r4.status_code == 422   # chỉ chuyển được client personal


# ── Hai mặt phẳng không dùng chéo ───────────────────────────────────────────────

async def test_user_token_is_not_a_machine_credential(env):
    u = await env.verified_user()
    token = u["headers"]["Authorization"].split(" ", 1)[1]
    r = await env.http.post("/v1/integrations/handshake", headers={"Authorization": f"Bearer {token}"},
                            json={"installation": {"device_id": "d"}})
    assert r.status_code == 401


async def test_api_key_is_not_a_user_token(env):
    tool = await env.tool()
    for path in ("/clients", "/audit"):
        r = await env.http.get(f"{BASE}{path}", headers={"Authorization": f"Bearer {tool.api_key}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID", path


@pytest.mark.parametrize("why", ["none", "chu_ky_gia", "hs256", "het_han", "sai_aud", "sai_iss", "kid_la", "thieu_exp"])
async def test_forged_or_invalid_user_tokens_are_rejected(env, why):
    sub = "a" * 32
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + 300, "role": "viewer", "perms": []}
    if why == "none":
        token = "khong_phai_jwt"
    elif why == "chu_ky_gia":
        fake = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(claims, fake, algorithm="RS256", headers={"kid": "kid-test-1"})
    elif why == "hs256":
        token = jwt.encode(claims, "bat-ky-chuoi-nao", algorithm="HS256")
    elif why == "het_han":
        claims["exp"] = now - 10
        token = jwt.encode(claims, env.identity.key, algorithm="RS256", headers={"kid": env.identity.kid})
    elif why == "sai_aud":
        claims["aud"] = "khac"
        token = jwt.encode(claims, env.identity.key, algorithm="RS256", headers={"kid": env.identity.kid})
    elif why == "sai_iss":
        claims["iss"] = "https://ke-gia.test"
        token = jwt.encode(claims, env.identity.key, algorithm="RS256", headers={"kid": env.identity.kid})
    elif why == "kid_la":
        token = jwt.encode(claims, env.identity.key, algorithm="RS256", headers={"kid": "kid-la"})
    else:
        del claims["exp"]
        token = jwt.encode(claims, env.identity.key, algorithm="RS256", headers={"kid": env.identity.kid})
    r = await env.http.get(f"{BASE}/clients", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert (await env.http.get(f"{BASE}/audit", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_missing_authorization_header(env):
    for method, path in (("get", "/clients"), ("post", "/clients"), ("get", "/audit")):
        r = await getattr(env.http, method)(f"{BASE}{path}")
        assert r.status_code == 401
        assert r.json()["ok"] is False and "request_id" in r.json()["meta"]
