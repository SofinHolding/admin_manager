"""Test end-to-end `/v1/reward/credentials` (plan C.2) qua ASGI transport + fake Discord offline."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from tests.conftest import insert_account, sign_token
from tests.harness.fake_discord import start_fake

GUILD = "111111111111111111"
CHANNEL = "222222222222222222"


@pytest.fixture
async def fake():
    f = await start_fake()
    try:
        yield f
    finally:
        await f.close()


@pytest.fixture
async def client(db_conn, fake):
    os.environ["REWARD_API_BASE"] = fake.api_base
    os.environ["REWARD_GATEWAY_URL"] = fake.gateway_url
    import importlib
    import main as main_module
    importlib.reload(main_module)
    app = main_module.app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac


async def test_put_credentials_valid_token_saves_ciphertext_status_valid(client, db_conn, fake):
    fake.valid_tokens = {"good-token": {"id": "u1", "username": "tester"}}
    account = await insert_account(db_conn, role="discord")
    token = sign_token(account)
    resp = await client.put(
        "/v1/reward/credentials", headers={"Authorization": f"Bearer {token}"},
        json={"token": "good-token", "guild_id": GUILD, "channel_id": CHANNEL, "command_name": fake.command_name})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "valid"
    assert body["discord_username"] == "tester"
    assert body["command"]["application_id"] == fake.app_id

    row = await db_conn.fetchrow(
        "SELECT token_ciphertext, status, discord_user_id FROM reward_discord_credentials WHERE account_id=$1",
        account["id"])
    assert row["status"] == "valid"
    assert row["discord_user_id"] == "u1"
    assert row["token_ciphertext"] != "good-token"
    assert "good-token" not in row["token_ciphertext"]


async def test_put_credentials_invalid_token_400_not_saved(client, db_conn, fake):
    fake.valid_tokens = {"good-token": {"id": "u1", "username": "tester"}}
    account = await insert_account(db_conn, role="discord")
    token = sign_token(account)
    resp = await client.put(
        "/v1/reward/credentials", headers={"Authorization": f"Bearer {token}"},
        json={"token": "bad-token", "guild_id": GUILD, "channel_id": CHANNEL})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Token Discord không hợp lệ"
    row = await db_conn.fetchrow(
        "SELECT * FROM reward_discord_credentials WHERE account_id=$1", account["id"])
    assert row is None
    assert "bad-token" not in resp.text


async def test_get_credentials_never_returns_token(client, db_conn, fake):
    fake.valid_tokens = None  # mọi token hợp lệ (giá trị mặc định)
    account = await insert_account(db_conn, role="discord")
    token = sign_token(account)
    headers = {"Authorization": f"Bearer {token}"}
    put_resp = await client.put(
        "/v1/reward/credentials", headers=headers,
        json={"token": "abc-real-token", "guild_id": GUILD, "channel_id": CHANNEL, "command_name": fake.command_name})
    assert put_resp.status_code == 200
    assert "abc-real-token" not in put_resp.text

    get_resp = await client.get("/v1/reward/credentials", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["exists"] is True
    assert body["status"] == "valid"
    assert "token" not in body
    assert "token_ciphertext" not in body
    assert "abc-real-token" not in get_resp.text


async def test_put_credentials_no_token_keeps_existing_and_updates_config(client, db_conn, fake):
    fake.valid_tokens = None
    account = await insert_account(db_conn, role="discord")
    token = sign_token(account)
    headers = {"Authorization": f"Bearer {token}"}
    await client.put(
        "/v1/reward/credentials", headers=headers,
        json={"token": "first-token", "guild_id": GUILD, "channel_id": CHANNEL, "command_name": fake.command_name})
    row1 = await db_conn.fetchrow(
        "SELECT token_ciphertext FROM reward_discord_credentials WHERE account_id=$1", account["id"])

    resp2 = await client.put(
        "/v1/reward/credentials", headers=headers,
        json={"guild_id": GUILD, "channel_id": CHANNEL, "command_name": fake.command_name, "delay_ms": 1234})
    assert resp2.status_code == 200
    row2 = await db_conn.fetchrow(
        "SELECT token_ciphertext, delay_ms FROM reward_discord_credentials WHERE account_id=$1", account["id"])
    assert row2["token_ciphertext"] == row1["token_ciphertext"]
    assert row2["delay_ms"] == 1234


async def test_delete_credentials_blocked_when_job_running(client, db_conn, fake):
    fake.valid_tokens = None
    account = await insert_account(db_conn, role="discord")
    token = sign_token(account)
    headers = {"Authorization": f"Bearer {token}"}
    await client.put(
        "/v1/reward/credentials", headers=headers,
        json={"token": "tok", "guild_id": GUILD, "channel_id": CHANNEL, "command_name": fake.command_name})
    await db_conn.execute(
        "INSERT INTO reward_jobs (account_id, name, status, guild_id, channel_id, command_name, total_items) "
        "VALUES ($1,'j','running',$2,$3,$4,0)", account["id"], GUILD, CHANNEL, fake.command_name)

    resp = await client.delete("/v1/reward/credentials", headers=headers)
    assert resp.status_code == 409


async def test_viewer_gets_403_on_credentials(client, db_conn):
    viewer = await insert_account(db_conn, role="viewer")
    token = sign_token(viewer)
    resp = await client.get("/v1/reward/credentials", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
