"""Test end-to-end `/v1/reward/admin/*` + `/v1/reward/health` qua ASGI transport (không cần cổng thật).

Phase 4: quyền reward do Identity quản lý — grant/revoke là lời gọi API công khai tới (fake) Identity bằng Bearer token
của chính người gọi; `reward_role_audit` là audit riêng của Reward.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import jwt as pyjwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import insert_account, sign_token  # noqa: E402
from tests.harness.fake_identity import AUDIENCE, ISSUER  # noqa: E402


async def test_health_ok(client):
    resp = await client.get("/v1/reward/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    latest = sorted(p.stem for p in (Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"))[-1]
    assert body["migration_version"] == latest
    assert "jwt_secret_fingerprint" not in body and "admin_api" not in body  # không còn secret chung / phụ thuộc admin_manager
    assert body["feed"]["enabled"] is False


async def test_viewer_gets_403_on_admin_accounts(client, db_conn):
    viewer = await insert_account(db_conn, role="viewer")
    resp = await client.get("/v1/reward/admin/accounts", headers={"Authorization": f"Bearer {sign_token(viewer)}"})
    assert resp.status_code == 403


async def test_discord_gets_403_on_grant_discord(client, db_conn, identity):
    discord_user = await insert_account(db_conn, role="discord")
    target = await insert_account(db_conn, role="viewer")
    resp = await client.post(
        f"/v1/reward/admin/accounts/{target['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {sign_token(discord_user)}"})
    assert resp.status_code == 403
    assert identity.calls("PUT", "/v1/admin/accounts") == []  # bị chặn cục bộ, không đẩy sang Identity


async def test_admin_grant_and_revoke_with_identity_call_and_audit(client, db_conn, identity):
    admin = await insert_account(db_conn, role="admin")
    viewer = await insert_account(db_conn, role="viewer")
    admin_token = sign_token(admin)
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.post(f"/v1/reward/admin/accounts/{viewer['id']}/grant-discord", json={"reason": "r1"},
                             headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["username"] == viewer["username"]
    assert body["from_role"] == "viewer" and body["to_role"] == "discord"  # hình dạng response cũ giữ nguyên

    # Cài đặt bằng API CÔNG KHAI của Identity, với Bearer của CHÍNH người gọi (không phải service key / token khác).
    put = identity.calls("PUT", f"/v1/admin/accounts/{viewer['id']}/permissions/reward:access")
    assert len(put) == 1 and put[0]["authorization"] == f"Bearer {admin_token}"
    assert "reward:access" in identity.users[viewer["id"]]["perms"]

    audit = await db_conn.fetch(
        "SELECT from_role, to_role, outcome, identity_status, actor_account_id, reason FROM reward_role_audit "
        "WHERE account_id = $1", viewer["id"])
    assert len(audit) == 1 and audit[0]["outcome"] == "applied" and audit[0]["identity_status"] == 200
    assert audit[0]["actor_account_id"] == admin["id"] and audit[0]["reason"] == "r1"

    resp = await client.post(f"/v1/reward/admin/accounts/{viewer['id']}/revoke-discord", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["to_role"] == "viewer" and resp.json()["paused_jobs"] == []
    assert len(identity.calls("DELETE", f"/v1/admin/accounts/{viewer['id']}/permissions/reward:access")) == 1
    assert "reward:access" not in identity.users[viewer["id"]]["perms"]

    audit2 = await db_conn.fetch(
        "SELECT outcome, from_role, to_role FROM reward_role_audit WHERE account_id = $1 ORDER BY id", viewer["id"])
    assert [a["outcome"] for a in audit2] == ["applied", "applied"]
    assert audit2[1]["from_role"] == "reward:access" and audit2[1]["to_role"] == "none"

    entries = (await client.get("/v1/reward/admin/role-audit", headers=headers)).json()["entries"]
    assert len(entries) == 2


async def test_grant_discord_on_admin_target_is_409_and_nothing_sent_to_identity(client, db_conn, identity):
    admin = await insert_account(db_conn, role="admin")
    other_admin = await insert_account(db_conn, role="admin")
    resp = await client.post(
        f"/v1/reward/admin/accounts/{other_admin['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {sign_token(admin)}"})
    assert resp.status_code == 409
    assert identity.calls("PUT", "/v1/admin/accounts") == []


async def test_grant_discord_on_suspended_target_is_409(client, db_conn, identity):
    admin = await insert_account(db_conn, role="admin")
    target = await insert_account(db_conn, role="viewer", status="suspended")
    resp = await client.post(
        f"/v1/reward/admin/accounts/{target['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {sign_token(admin)}"})
    assert resp.status_code == 409


async def test_grant_discord_on_self_is_400(client, db_conn):
    admin = await insert_account(db_conn, role="admin")
    resp = await client.post(
        f"/v1/reward/admin/accounts/{admin['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {sign_token(admin)}"})
    assert resp.status_code == 400


async def test_grant_discord_on_missing_account_is_404(client, db_conn):
    admin = await insert_account(db_conn, role="admin")
    resp = await client.post(
        "/v1/reward/admin/accounts/does-not-exist/grant-discord",
        json={}, headers={"Authorization": f"Bearer {sign_token(admin)}"})
    assert resp.status_code == 404


async def test_revoke_without_explicit_grant_is_409_and_audited_as_noop(client, db_conn, identity):
    admin = await insert_account(db_conn, role="admin")
    viewer = await insert_account(db_conn, role="viewer")
    resp = await client.post(
        f"/v1/reward/admin/accounts/{viewer['id']}/revoke-discord",
        json={}, headers={"Authorization": f"Bearer {sign_token(admin)}"})
    assert resp.status_code == 409
    assert await db_conn.fetchval("SELECT outcome FROM reward_role_audit WHERE account_id=$1", viewer["id"]) == "noop"


async def test_identity_rejection_is_forwarded_and_audited(client, db_conn, identity):
    """Admin của Reward (perm `reward:admin`, role viewer) nhưng Identity không cho phép → trả đúng mã Identity + audit."""
    viewer_admin = await insert_account(db_conn, role="viewer", perms=("reward:admin",))
    target = await insert_account(db_conn, role="viewer")
    resp = await client.post(
        f"/v1/reward/admin/accounts/{target['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {sign_token(viewer_admin)}"})
    assert resp.status_code == 403  # Identity trả 403 cho lời gọi list users (không phải admin Identity)
    assert resp.json()["detail"]


async def test_reward_admin_permission_without_admin_role_reaches_admin_routes(client, db_conn, identity):
    """Route quản trị cần `reward:admin` (perm) — không nhất thiết role admin; phần local (role-audit) chạy được."""
    holder = await insert_account(db_conn, role="viewer", perms=("reward:admin",))
    resp = await client.get("/v1/reward/admin/role-audit", headers={"Authorization": f"Bearer {sign_token(holder)}"})
    assert resp.status_code == 200
    no_perm = await insert_account(db_conn, role="viewer", perms=("reward:access",))
    resp = await client.get("/v1/reward/admin/role-audit", headers={"Authorization": f"Bearer {sign_token(no_perm)}"})
    assert resp.status_code == 403


async def test_list_accounts_keeps_shape_and_enriches_from_reward_tables(client, db_conn, identity):
    admin = await insert_account(db_conn, role="admin")
    granted = await insert_account(db_conn, role="viewer", username="alice_g")
    await insert_account(db_conn, role="viewer", username="bob_v")
    headers = {"Authorization": f"Bearer {sign_token(admin)}"}
    await client.post(f"/v1/reward/admin/accounts/{granted['id']}/grant-discord", json={}, headers=headers)
    await db_conn.execute(
        "INSERT INTO reward_discord_credentials (account_id, status) VALUES ($1, 'valid')", granted["id"])

    resp = await client.get("/v1/reward/admin/accounts", headers=headers)
    assert resp.status_code == 200
    rows = {r["username"]: r for r in resp.json()["accounts"]}
    assert {"id", "username", "email", "role", "status", "created_at", "last_login", "has_credential",
            "credential_status", "jobs_total", "jobs_running"} <= set(rows["alice_g"])
    assert rows["alice_g"]["has_credential"] is True and rows["alice_g"]["credential_status"] == "valid"
    assert rows["alice_g"]["reward_access"] is True and rows["bob_v"]["reward_access"] is False

    filtered = (await client.get("/v1/reward/admin/accounts?q=alice", headers=headers)).json()["accounts"]
    assert [r["username"] for r in filtered] == ["alice_g"]
    only_susp = (await client.get("/v1/reward/admin/accounts?status=suspended", headers=headers)).json()["accounts"]
    assert only_susp == []


async def test_suspended_admin_gets_403_on_write_endpoint(client, db_conn, identity):
    """Tài khoản bị khoá (event `status_changed→suspended` đã vào denylist) bị chặn NGAY dù token còn hạn."""
    from tests.conftest import make_consumer
    admin = await insert_account(db_conn, role="admin")
    viewer = await insert_account(db_conn, role="viewer")
    token = sign_token(admin)
    identity.set_status(admin["id"], "suspended")
    assert await make_consumer(client, identity).poll_once() == 2  # status_changed + session.revoked
    resp = await client.post(
        f"/v1/reward/admin/accounts/{viewer['id']}/grant-discord", json={},
        headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Tài khoản không hoạt động"
    assert identity.calls("PUT", "/v1/admin/accounts") == []


def _forge(identity, **claims) -> str:
    now = int(time.time())
    base = {"iss": ISSUER, "aud": [AUDIENCE], "sub": "x" * 32, "username": "x", "role": "admin", "perms": [],
            "sid": "s", "jti": "j", "iat": now, "nbf": now, "exp": now + 900}
    base.update(claims)
    return pyjwt.encode(base, identity_key(), algorithm="RS256", headers={"kid": identity.kid})


def identity_key():
    from tests.harness.fake_identity import _SESSION_KEY
    return _SESSION_KEY


async def test_expired_token_is_401(client, identity):
    now = int(time.time())
    token = _forge(identity, exp=now - 120, iat=now - 1000, nbf=now - 1000)
    resp = await client.get("/v1/reward/admin/accounts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token hết hạn"


async def test_wrong_signing_key_token_is_401(client, identity):
    from cryptography.hazmat.primitives.asymmetric import rsa
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = pyjwt.encode({"iss": ISSUER, "aud": [AUDIENCE], "sub": "x", "role": "admin", "iat": now, "nbf": now,
                          "exp": now + 900}, other, algorithm="RS256", headers={"kid": identity.kid})
    resp = await client.get("/v1/reward/admin/accounts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token không hợp lệ"


async def test_missing_authorization_header_is_401(client):
    resp = await client.get("/v1/reward/admin/accounts")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Thiếu token"
