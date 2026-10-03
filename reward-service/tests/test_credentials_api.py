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


async def _account(db_conn):
    account = await insert_account(db_conn, role="discord")
    return account, {"Authorization": f"Bearer {sign_token(account)}"}


def _cfg(fake, **kw) -> dict:
    return {"guild_id": GUILD, "channel_id": CHANNEL, "command_name": fake.command_name, **kw}


async def test_add_valid_token_saves_ciphertext_and_config_becomes_valid(client, db_conn, fake):
    fake.valid_tokens = {"good-token": {"id": "u1", "username": "tester"}}
    account, headers = await _account(db_conn)
    put = await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    assert put.status_code == 200, put.text
    assert put.json()["status"] == "unverified"  # chưa có token nào

    resp = await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "good-token"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]["status"] == "valid"
    assert body["token"]["discord_username"] == "tester"
    assert body["command"]["application_id"] == fake.app_id
    assert "good-token" not in resp.text

    row = await db_conn.fetchrow(
        "SELECT token_ciphertext, status, discord_user_id FROM reward_discord_tokens WHERE account_id=$1",
        account["id"])
    assert row["status"] == "valid" and row["discord_user_id"] == "u1"
    assert row["token_ciphertext"] != "good-token" and "good-token" not in row["token_ciphertext"]
    cfg = await db_conn.fetchrow("SELECT status FROM reward_discord_credentials WHERE account_id=$1", account["id"])
    assert cfg["status"] == "valid"


async def test_add_invalid_token_400_not_saved(client, db_conn, fake):
    fake.valid_tokens = {"good-token": {"id": "u1", "username": "tester"}}
    account, headers = await _account(db_conn)
    await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    resp = await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "bad-token"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Token Discord không hợp lệ"
    assert await db_conn.fetchval("SELECT count(*) FROM reward_discord_tokens WHERE account_id=$1", account["id"]) == 0
    assert "bad-token" not in resp.text


async def test_get_credentials_lists_tokens_but_never_returns_token(client, db_conn, fake):
    fake.valid_tokens = None
    _account_row, headers = await _account(db_conn)
    await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    add = await client.post(
        "/v1/reward/credentials/tokens", headers=headers, json={"token": "abc-real-token", "label": "acc1"})
    assert add.status_code == 200
    assert "abc-real-token" not in add.text

    get_resp = await client.get("/v1/reward/credentials", headers=headers)
    body = get_resp.json()
    assert body["exists"] is True and body["status"] == "valid"
    assert [t["label"] for t in body["tokens"]] == ["acc1"]
    assert "abc-real-token" not in get_resp.text
    assert "token_ciphertext" not in get_resp.text


async def test_multiple_tokens_and_duplicate_discord_account_rejected(client, db_conn, fake):
    fake.valid_tokens = {"t1": {"id": "u1", "username": "one"}, "t2": {"id": "u2", "username": "two"},
                         "t1-alias": {"id": "u1", "username": "one"}}
    _a, headers = await _account(db_conn)
    await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    assert (await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "t1"})).status_code == 200
    assert (await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "t2"})).status_code == 200
    dup = await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "t1-alias"})
    assert dup.status_code == 409  # cùng tài khoản Discord → xoay vòng với chính nó là vô nghĩa
    tokens = (await client.get("/v1/reward/credentials/tokens", headers=headers)).json()["tokens"]
    assert [t["discord_username"] for t in tokens] == ["one", "two"]


async def test_wrong_guild_invalidates_tokens_and_fixing_it_recovers(client, db_conn, fake):
    # Đúng kịch bản trên VPS: sửa nhầm Guild ID → "không tìm thấy lệnh" → config invalid → nút Chạy bị chặn.
    fake.valid_tokens = None
    _a, headers = await _account(db_conn)
    await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "tok"})

    bad = await client.put(
        "/v1/reward/credentials", headers=headers, json=_cfg(fake, command_name="khong-ton-tai"))
    assert bad.json()["status"] == "invalid"
    assert "khong-ton-tai" in bad.json()["last_error"]
    assert bad.json()["tokens"][0]["status"] == "invalid"

    fixed = await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    assert fixed.json()["status"] == "valid"
    assert fixed.json()["tokens"][0]["status"] == "valid"


async def test_disable_and_delete_token_update_config_status(client, db_conn, fake):
    fake.valid_tokens = None
    account, headers = await _account(db_conn)
    await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    tid = (await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "tok"})).json()["token"]["id"]

    off = await client.patch(f"/v1/reward/credentials/tokens/{tid}", headers=headers, json={"enabled": False})
    assert off.json()["token"]["enabled"] is False
    cfg = await client.get("/v1/reward/credentials", headers=headers)
    assert cfg.json()["status"] == "invalid"  # không còn token nào bật + hợp lệ

    await db_conn.execute(
        "INSERT INTO reward_jobs (account_id, name, status, guild_id, channel_id, command_name, total_items) "
        "VALUES ($1,'j','running',$2,$3,$4,0)", account["id"], GUILD, CHANNEL, fake.command_name)
    assert (await client.delete(f"/v1/reward/credentials/tokens/{tid}", headers=headers)).status_code == 409
    await db_conn.execute("DELETE FROM reward_jobs WHERE account_id=$1", account["id"])

    assert (await client.delete(f"/v1/reward/credentials/tokens/{tid}", headers=headers)).status_code == 200
    assert (await client.delete(f"/v1/reward/credentials/tokens/{tid}", headers=headers)).status_code == 404
    assert (await client.get("/v1/reward/credentials", headers=headers)).json()["status"] == "unverified"


async def test_delete_credentials_blocked_when_job_running(client, db_conn, fake):
    fake.valid_tokens = None
    account, headers = await _account(db_conn)
    await client.put("/v1/reward/credentials", headers=headers, json=_cfg(fake))
    await client.post("/v1/reward/credentials/tokens", headers=headers, json={"token": "tok"})
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
