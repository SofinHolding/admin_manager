"""Client + credential (mặt phẳng USER): tạo/xem/phân quyền, cấp/xoay/thu hồi API key, điều kiện email, admin."""

from __future__ import annotations

import re
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


async def create_client(env, user, **over):
    body = {"tool_name": "T", "client_kind": "primary_tool", "platforms": ["facebook"], **over}
    return await env.http.post(f"{BASE}/clients", headers=user["headers"], json=body)


# ── Tạo client ──────────────────────────────────────────────────────────────────

async def test_verified_account_gets_active_client_and_unverified_gets_pending(env):
    good = await env.verified_user()
    r = await create_client(env, good)
    assert r.status_code == 201 and r.json()["data"]["status"] == "active"
    assert r.json()["data"]["owner_account_id"] == good["sub"]
    assert re.match(r"^cli_[0-9a-f]{32}$", r.json()["data"]["client_id"])

    pending = env.user()
    await env.projection(pending["sub"], verified=False, status="pending")
    assert (await create_client(env, pending)).json()["data"]["status"] == "pending_email_verification"
    unknown = env.user()  # chưa có projection: coi là CHƯA xác thực (an toàn mặc định)
    assert (await create_client(env, unknown)).json()["data"]["status"] == "pending_email_verification"


async def test_platforms_are_clamped_to_registry_and_scopes_follow_kind(env):
    u = await env.verified_user()
    d = (await create_client(env, u, platforms=["facebook", "khong_co", "youtube"])).json()["data"]
    assert d["allowed_platforms"] == ["facebook", "youtube"]
    assert set(d["allowed_scopes"]) == {"integration:handshake", "resources:write", "snapshots:write", "metrics:write"}
    fb = (await create_client(env, u, client_kind="fallback_tool")).json()["data"]
    assert {"jobs:claim", "jobs:complete"} <= set(fb["allowed_scopes"]) and "jobs:claim:system" not in fb["allowed_scopes"]
    r = await create_client(env, u, platforms=["khong_co"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"


async def test_client_body_rejects_owner_and_unknown_fields_and_bad_kind(env):
    u = await env.verified_user()
    r = await create_client(env, u, owner_account_id="acc_gia_mao")
    assert r.status_code == 422 and r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert "owner" in r.json()["error"]["message"]
    assert "acc_gia_mao" not in r.text   # không phản chiếu giá trị đầu vào
    assert (await create_client(env, u, client_kind="root")).status_code == 422
    assert (await create_client(env, u, platforms=[])).status_code == 422


async def test_suspended_account_cannot_create_clients(env):
    u = env.user()
    await env.projection(u["sub"], status="suspended")
    r = await create_client(env, u)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_INACTIVE"


async def test_client_limit_per_owner(make_env):
    env, _ = await make_env(max_clients_per_owner=2)
    u = await env.verified_user()
    assert (await create_client(env, u)).status_code == 201
    assert (await create_client(env, u)).status_code == 201
    r = await create_client(env, u)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LIMIT_EXCEEDED"


# ── Xem / phân quyền ────────────────────────────────────────────────────────────

async def test_list_and_get_visibility(env):
    a, b = await env.verified_user(), await env.verified_user()
    admin = await env.verified_user(role="admin")
    ca = (await create_client(env, a)).json()["data"]["client_id"]
    cb = (await create_client(env, b)).json()["data"]["client_id"]

    mine = (await env.http.get(f"{BASE}/clients", headers=a["headers"])).json()["data"]
    assert [c["client_id"] for c in mine] == [ca]
    spoof = (await env.http.get(f"{BASE}/clients", headers=a["headers"], params={"owner": b["sub"]})).json()["data"]
    assert spoof == []                                         # không xem được client của người khác bằng ?owner=
    allc = (await env.http.get(f"{BASE}/clients", headers=admin["headers"])).json()["data"]
    assert {ca, cb} <= {c["client_id"] for c in allc}
    only_b = (await env.http.get(f"{BASE}/clients", headers=admin["headers"], params={"owner": b["sub"]})).json()["data"]
    assert [c["client_id"] for c in only_b] == [cb]

    r = await env.http.get(f"{BASE}/clients/{cb}", headers=a["headers"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "CLIENT_NOT_FOUND"   # không lộ sự tồn tại
    assert (await env.http.get(f"{BASE}/clients/{cb}", headers=admin["headers"])).status_code == 200
    assert (await env.http.get(f"{BASE}/clients/cli_khongco", headers=a["headers"])).status_code == 404


async def test_client_detail_never_exposes_secret_or_hash(env):
    tool = await env.tool()
    user = {"headers": env.user(sub=tool.owner)["headers"], "sub": tool.owner}
    body = (await env.http.get(f"{BASE}/clients/{tool.client_id}", headers=user["headers"])).json()["data"]
    cred = body["credentials"][0]
    assert cred["key_id"] == tool.key_id and cred["status"] == "active" and cred["last_used_at"] is None
    text = str(body)
    assert tool.api_key not in text and "secret" not in text and "hash" not in text and tool.api_key.split(".")[1] not in text


# ── Cấp credential ──────────────────────────────────────────────────────────────

async def test_issue_shows_plaintext_once_and_stores_only_hash(env):
    tool = await env.tool()
    m = API_KEY_RE.match(tool.api_key)
    assert m and m.group(1) == tool.key_id
    secret = m.group(2)
    row = (await env.rows("SELECT * FROM integration_credentials WHERE key_id = $1", tool.key_id))[0]
    assert row["secret_hash"] == sha256_hex(secret)
    assert secret not in str(row) and tool.api_key not in str(row)
    # Plaintext không nằm ở BẤT KỲ bảng nào (kể cả audit và outbox).
    for table in ("integration_credentials", "integration_clients", "audit_log", "outbox", "installations"):
        dump = str(await env.rows(f"SELECT * FROM {table}"))
        assert secret not in dump and tool.api_key not in dump, table


async def test_pending_client_cannot_get_credentials_until_email_verified(env):
    u = env.user()
    await env.projection(u["sub"], verified=False, status="pending")
    cid = (await create_client(env, u)).json()["data"]["client_id"]
    r = await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"], json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert await env.scalar("SELECT count(*) FROM integration_credentials") == 0


async def test_only_owner_can_issue_even_admin_cannot(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    other = await env.verified_user()
    for u in (admin, other):
        r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=u["headers"], json={})
        assert r.status_code == 404 and r.json()["error"]["code"] == "CLIENT_NOT_FOUND"


async def test_dash_admin_can_view_but_not_rotate_or_revoke_other_owners_credentials(env):
    """`dash_admin` chỉ XEM (is_admin gồm cả hai role). Xoay credential trả API key plaintext mới => chỉ chủ client hoặc
    role `admin`; thu hồi cũng vậy. Người lạ vẫn nhận 404 (không lộ sự tồn tại)."""
    tool = await env.tool()
    dash = await env.verified_user(role="dash_admin")
    stranger = await env.verified_user()
    cred = f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}"
    assert (await env.http.get(f"{BASE}/clients/{tool.client_id}", headers=dash["headers"])).status_code == 200   # xem được
    r = await env.http.post(f"{cred}/rotate", headers=dash["headers"], json={"grace_seconds": 0})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED" and "api_key" not in r.text
    r = await env.http.delete(cred, headers=dash["headers"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"
    for call in (env.http.post(f"{cred}/rotate", headers=stranger["headers"], json={}),
                 env.http.delete(cred, headers=stranger["headers"])):
        r = await call
        assert r.status_code == 404 and r.json()["error"]["code"] == "CLIENT_NOT_FOUND"
    # không có gì đổi: credential vẫn active, không có credential mới
    assert await env.scalar("SELECT status FROM integration_credentials WHERE key_id = $1", tool.key_id) == "active"
    assert await env.scalar("SELECT count(*) FROM integration_credentials") == 1


async def test_admin_and_owner_can_rotate_and_revoke(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate", headers=admin["headers"],
                            json={"grace_seconds": 0})
    assert r.status_code == 201 and r.json()["data"]["api_key"].startswith("mpk_")
    new_key = r.json()["data"]["key_id"]
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{new_key}", headers=admin["headers"])
    assert r.status_code == 200 and r.json()["data"]["status"] == "revoked"
    owner = env.user(sub=tool.owner)
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"], json={})
    assert r.status_code == 201
    k2 = r.json()["data"]["key_id"]
    assert (await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{k2}/rotate", headers=owner["headers"],
                                json={"grace_seconds": 0})).status_code == 201
    assert (await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{k2}", headers=owner["headers"])).status_code == 200


async def test_requested_scopes_cannot_exceed_client_scopes(env):
    u = await env.verified_user()
    cid = (await create_client(env, u)).json()["data"]["client_id"]
    r = await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"],
                            json={"scopes": ["snapshots:write", "jobs:claim:system"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"
    ok = await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"],
                             json={"scopes": ["snapshots:write"], "label": "pc"})
    assert ok.status_code == 201 and ok.json()["data"]["scopes"] == ["snapshots:write"]
    assert ok.headers["cache-control"] == "no-store"


async def test_legacy_alias_path_issues_credentials(env):
    u = await env.verified_user()
    cid = (await create_client(env, u)).json()["data"]["client_id"]
    r = await env.http.post(f"{BASE}/{cid}/credentials", headers=u["headers"], json={})
    assert r.status_code == 201 and r.json()["data"]["api_key"].startswith("mpk_")


async def test_credential_count_limit(make_env):
    env, _ = await make_env(max_credentials_per_client=2)
    u = await env.verified_user()
    cid = (await create_client(env, u)).json()["data"]["client_id"]
    for _ in range(2):
        assert (await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"], json={})).status_code == 201
    r = await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"], json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LIMIT_EXCEEDED"


# ── Dùng key: hết hạn / thu hồi / xoay ──────────────────────────────────────────

async def whoami(tool):
    return await tool.post("/v1/integrations/handshake", {"installation": {"device_id": "d1"}})


async def test_expired_key_is_rejected_with_auth_invalid(env, clock):
    u = await env.verified_user()
    cid = (await create_client(env, u)).json()["data"]["client_id"]
    d = (await env.http.post(f"{BASE}/clients/{cid}/credentials", headers=u["headers"],
                             json={"expires_in_days": 1})).json()["data"]
    from conftest import Tool
    tool = Tool(env.http, cid, u["sub"], d["key_id"], d["api_key"], d["scopes"])
    assert (await whoami(tool)).status_code == 200
    clock.advance(days=1, seconds=1)
    r = await whoami(tool)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID"


async def test_revoke_takes_effect_immediately_and_is_idempotent(env):
    tool = await env.tool()
    assert (await whoami(tool)).status_code == 200
    owner = {"headers": env.user(sub=tool.owner)["headers"]}
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}", headers=owner["headers"])
    assert r.status_code == 200 and r.json()["data"] == {"client_id": tool.client_id, "key_id": tool.key_id,
                                                         "status": "revoked", "already_revoked": False}
    denied = await whoami(tool)
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "AUTH_REVOKED"
    again = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}", headers=owner["headers"])
    assert again.json()["data"]["already_revoked"] is True
    assert (await outbox_types(env)).count("credential.revoked") == 1   # chỉ phát một lần
    ev = (await env.rows("SELECT data FROM outbox WHERE type = 'credential.revoked'"))[0]["data"]
    assert ev == {"client_id": tool.client_id, "key_id": tool.key_id}


async def test_revoke_unknown_or_foreign_key(env):
    tool = await env.tool()
    stranger = await env.verified_user()
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}", headers=stranger["headers"])
    assert r.status_code == 404
    owner = env.user(sub=tool.owner)
    r = await env.http.delete(f"{BASE}/clients/{tool.client_id}/credentials/khongco12345", headers=owner["headers"])
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
    fresh = Tool(env.http, tool.client_id, tool.owner, new["key_id"], new["api_key"], new["scopes"])
    assert (await whoami(fresh)).status_code == 200 and (await whoami(tool)).status_code == 200
    clock.advance(seconds=301)
    expired = await whoami(tool)
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "AUTH_INVALID"
    assert (await whoami(fresh)).status_code == 200


async def test_rotate_without_grace_revokes_old_key_immediately(env):
    tool = await env.tool()
    owner = env.user(sub=tool.owner)
    new = (await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate",
                               headers=owner["headers"], json={})).json()["data"]
    r = await whoami(tool)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REVOKED"
    from conftest import Tool
    assert (await whoami(Tool(env.http, tool.client_id, tool.owner, new["key_id"], new["api_key"], []))).status_code == 200
    assert "credential.revoked" in await outbox_types(env)
    # xoay một khoá đã thu hồi => lỗi rõ ràng
    r = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials/{tool.key_id}/rotate",
                            headers=owner["headers"], json={})
    assert r.status_code == 409


async def test_last_used_at_is_recorded_and_throttled(env, clock):
    tool = await env.tool()
    assert await env.scalar("SELECT last_used_at FROM integration_credentials WHERE key_id = $1", tool.key_id) is None
    await whoami(tool)
    first = await env.scalar("SELECT last_used_at FROM integration_credentials WHERE key_id = $1", tool.key_id)
    assert first == clock()
    clock.advance(seconds=10)
    await whoami(tool)
    assert await env.scalar("SELECT last_used_at FROM integration_credentials WHERE key_id = $1", tool.key_id) == first
    clock.advance(seconds=61)
    await whoami(tool)
    assert await env.scalar("SELECT last_used_at FROM integration_credentials WHERE key_id = $1", tool.key_id) > first


# ── Admin ───────────────────────────────────────────────────────────────────────

async def test_patch_client_is_admin_only(env):
    tool = await env.tool()
    for role in ("viewer", "dash_admin", "discord"):
        u = await env.verified_user(role=role)
        r = await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=u["headers"], json={"status": "suspended"})
        assert r.status_code == 403, role
    owner = env.user(sub=tool.owner)
    assert (await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=owner["headers"],
                                 json={"status": "suspended"})).status_code == 403   # chủ cũng không tự gỡ giới hạn


async def test_system_scope_only_for_fallback_tool_and_only_by_admin(env):
    admin = await env.verified_user(role="admin")
    u = await env.verified_user()
    prim = (await create_client(env, u)).json()["data"]["client_id"]
    fall = (await create_client(env, u, client_kind="fallback_tool")).json()["data"]["client_id"]
    r = await env.http.patch(f"{BASE}/clients/{prim}", headers=admin["headers"],
                             json={"allowed_scopes": ["integration:handshake", "jobs:claim:system"]})
    assert r.status_code == 422
    r = await env.http.patch(f"{BASE}/clients/{fall}", headers=admin["headers"],
                             json={"allowed_scopes": ["integration:handshake", "jobs:claim", "jobs:claim:system"]})
    assert r.status_code == 200 and "jobs:claim:system" in r.json()["data"]["allowed_scopes"]
    assert (await env.http.patch(f"{BASE}/clients/{fall}", headers=admin["headers"],
                                 json={"allowed_scopes": ["nope:write"]})).status_code == 422
    assert (await env.http.patch(f"{BASE}/clients/{prim}", headers=admin["headers"],
                                 json={"allowed_scopes": ["jobs:claim"]})).status_code == 422
    assert (await env.http.patch(f"{BASE}/clients/{prim}", headers=admin["headers"], json={"owner_account_id": "x"})).status_code == 422


async def test_narrowing_client_scopes_applies_to_existing_keys_at_once(env):
    tool = await env.tool()
    assert (await tool.post("/v1/integrations/snapshots", _snap())).status_code == 201
    admin = await env.verified_user(role="admin")
    await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"],
                         json={"allowed_scopes": ["integration:handshake"]})
    r = await tool.post("/v1/integrations/snapshots", _snap())
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"


def _snap():
    return {"platform": "facebook", "snapshot_type": "delta", "observed_at": "2026-10-05T10:00:00Z",
            "report_timezone": "UTC", "expected_counts": {}}


async def test_suspend_revokes_every_credential_and_blocks_new_ones(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    owner = env.user(sub=tool.owner)
    r = await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"], json={"status": "suspended"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "suspended"
    denied = await whoami(tool)
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "AUTH_REVOKED"
    again = await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"], json={})
    assert again.status_code == 403 and again.json()["error"]["code"] == "CLIENT_NOT_ACTIVE"
    assert "credential.revoked" in await outbox_types(env)
    # kích hoạt lại: credential cũ đã bị thu hồi vĩnh viễn, phải cấp key mới
    assert (await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"],
                                 json={"status": "active"})).json()["data"]["status"] == "active"
    assert (await whoami(tool)).status_code == 401
    assert (await env.http.post(f"{BASE}/clients/{tool.client_id}/credentials", headers=owner["headers"], json={})).status_code == 201


async def test_admin_cannot_activate_client_of_unverified_account(env):
    u = env.user()
    await env.projection(u["sub"], verified=False, status="pending")
    cid = (await create_client(env, u)).json()["data"]["client_id"]
    admin = await env.verified_user(role="admin")
    r = await env.http.patch(f"{BASE}/clients/{cid}", headers=admin["headers"], json={"status": "active"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


async def test_deleted_client_stays_deleted(env):
    tool = await env.tool()
    admin = await env.verified_user(role="admin")
    await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"], json={"status": "deleted"})
    r = await env.http.patch(f"{BASE}/clients/{tool.client_id}", headers=admin["headers"], json={"status": "active"})
    assert r.status_code == 409


# ── Hai mặt phẳng không dùng chéo ───────────────────────────────────────────────

async def test_user_token_is_not_a_machine_credential(env):
    u = await env.verified_user()
    r = await env.http.post("/v1/integrations/handshake", headers=u["headers"], json={"installation": {"device_id": "d"}})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID"
    r = await env.http.post("/v1/integrations/snapshots", headers=u["headers"], json=_snap())
    assert r.status_code == 401


async def test_api_key_is_not_a_user_token(env):
    tool = await env.tool()
    for path in ("/clients", "/audit", "/admin/platforms"):
        r = await env.http.get(f"{BASE}{path}", headers={"Authorization": f"Bearer {tool.api_key}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID", path


@pytest.mark.parametrize("why", ["none", "chu_ky_gia", "hs256", "het_han", "sai_aud", "sai_iss", "kid_la", "thieu_exp"])
async def test_forged_or_invalid_user_tokens_are_rejected(env, why):
    sub = "a" * 32
    now = int(time.time())
    base = {"iss": ISSUER, "aud": [AUDIENCE], "sub": sub, "role": "admin", "iat": now, "nbf": now, "exp": now + 600}
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if why == "none":
        token = jwt.encode(base, key=None, algorithm="none", headers={"kid": env.identity.kid})
    elif why == "chu_ky_gia":
        token = env.identity.token(sub=sub, role="admin", key=attacker)
    elif why == "hs256":
        token = jwt.encode(base, "x" * 40, algorithm="HS256", headers={"kid": env.identity.kid})
    elif why == "het_han":
        token = env.identity.token(sub=sub, role="admin", exp=now - 3600, iat=now - 7200, nbf=now - 7200)
    elif why == "sai_aud":
        token = env.identity.token(sub=sub, role="admin", aud=["khac"])
    elif why == "sai_iss":
        token = env.identity.token(sub=sub, role="admin", iss="http://ke-tan-cong.test")
    elif why == "kid_la":
        token = env.identity.token(sub=sub, role="admin", kid="kid-la")
    else:
        token = jwt.encode({k: v for k, v in base.items() if k != "exp"}, env.identity.key, algorithm="RS256",
                           headers={"kid": env.identity.kid})
    r = await env.http.get(f"{BASE}/clients", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID", why
    assert (await env.http.get(f"{BASE}/audit", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_missing_authorization_header(env):
    for method, path in (("get", "/clients"), ("post", "/clients"), ("get", "/audit")):
        r = await getattr(env.http, method)(f"{BASE}{path}")
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID"
        assert r.json()["ok"] is False and "request_id" in r.json()["meta"]
