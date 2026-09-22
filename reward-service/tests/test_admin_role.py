"""Test end-to-end `/v1/reward/admin/*` + `/v1/reward/health` qua ASGI transport (không cần cổng thật)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import insert_account, sign_token  # noqa: E402


@pytest.fixture
async def client(db_conn):
    # Import main SAU khi conftest đã set biến môi trường test (JWT_SECRET/DATABASE_URL/...).
    import importlib
    import main as main_module
    importlib.reload(main_module)
    app = main_module.app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with main_module.app.router.lifespan_context(app):
            yield ac


async def test_health_ok(client):
    resp = await client.get("/v1/reward/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["migration_version"] == "001_reward_init"
    assert len(body["jwt_secret_fingerprint"]) == 8


async def test_viewer_gets_403_on_admin_accounts(client, db_conn):
    viewer = await insert_account(db_conn, role="viewer")
    token = sign_token(viewer)
    resp = await client.get("/v1/reward/admin/accounts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_discord_gets_403_on_grant_discord(client, db_conn):
    discord_user = await insert_account(db_conn, role="discord")
    target = await insert_account(db_conn, role="viewer")
    token = sign_token(discord_user)
    resp = await client.post(
        f"/v1/reward/admin/accounts/{target['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_admin_grant_and_revoke_discord_with_audit(client, db_conn):
    admin = await insert_account(db_conn, role="admin")
    viewer = await insert_account(db_conn, role="viewer")
    admin_token = sign_token(admin)
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.post(f"/v1/reward/admin/accounts/{viewer['id']}/grant-discord", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from_role"] == "viewer"
    assert body["to_role"] == "discord"

    role = await db_conn.fetchval("SELECT role FROM accounts WHERE id = $1", viewer["id"])
    assert role == "discord"

    audit_rows = await db_conn.fetch(
        "SELECT from_role, to_role FROM reward_role_audit WHERE account_id = $1", viewer["id"])
    assert len(audit_rows) == 1
    assert audit_rows[0]["from_role"] == "viewer"
    assert audit_rows[0]["to_role"] == "discord"

    resp = await client.post(f"/v1/reward/admin/accounts/{viewer['id']}/revoke-discord", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["to_role"] == "viewer"

    role_after = await db_conn.fetchval("SELECT role FROM accounts WHERE id = $1", viewer["id"])
    assert role_after == "viewer"

    audit_rows_2 = await db_conn.fetch(
        "SELECT from_role, to_role FROM reward_role_audit WHERE account_id = $1 ORDER BY at", viewer["id"])
    assert len(audit_rows_2) == 2
    assert audit_rows_2[1]["from_role"] == "discord"
    assert audit_rows_2[1]["to_role"] == "viewer"


async def test_grant_discord_on_admin_target_is_409_and_unchanged(client, db_conn):
    admin = await insert_account(db_conn, role="admin")
    other_admin = await insert_account(db_conn, role="admin")
    token = sign_token(admin)
    resp = await client.post(
        f"/v1/reward/admin/accounts/{other_admin['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409
    role = await db_conn.fetchval("SELECT role FROM accounts WHERE id = $1", other_admin["id"])
    assert role == "admin"


async def test_grant_discord_on_self_is_400(client, db_conn):
    admin = await insert_account(db_conn, role="admin")
    token = sign_token(admin)
    resp = await client.post(
        f"/v1/reward/admin/accounts/{admin['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


async def test_grant_discord_on_missing_account_is_404(client, db_conn):
    admin = await insert_account(db_conn, role="admin")
    token = sign_token(admin)
    resp = await client.post(
        "/v1/reward/admin/accounts/does-not-exist/grant-discord",
        json={}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


async def test_suspended_admin_gets_403_on_write_endpoint(client, db_conn):
    admin = await insert_account(db_conn, role="admin", status="suspended")
    viewer = await insert_account(db_conn, role="viewer")
    token = sign_token(admin)
    resp = await client.post(
        f"/v1/reward/admin/accounts/{viewer['id']}/grant-discord",
        json={}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Tài khoản không hoạt động"


async def test_expired_token_is_401():
    import jwt as pyjwt
    import time
    from tests.conftest import TEST_JWT_SECRET
    now = int(time.time())
    token = pyjwt.encode(
        {"sub": "x", "username": "x", "role": "admin", "exp": now - 5, "iat": now - 900},
        TEST_JWT_SECRET, algorithm="HS256")
    transport = httpx.ASGITransport(app=__import__("main").app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/reward/admin/accounts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token hết hạn"


async def test_wrong_secret_token_is_401():
    import jwt as pyjwt
    import time
    now = int(time.time())
    token = pyjwt.encode(
        {"sub": "x", "username": "x", "role": "admin", "exp": now + 900, "iat": now},
        "wrong-secret", algorithm="HS256")
    transport = httpx.ASGITransport(app=__import__("main").app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/reward/admin/accounts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token không hợp lệ"


async def test_missing_authorization_header_is_401():
    transport = httpx.ASGITransport(app=__import__("main").app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/reward/admin/accounts")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Thiếu token"
