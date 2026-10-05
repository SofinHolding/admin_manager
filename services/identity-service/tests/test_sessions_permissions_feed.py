"""Phiên thu hồi được, permission, transactional outbox và change feed nội bộ."""

from __future__ import annotations

import asyncio

import pytest

import identity_service.outbox.events as events_mod
from identity_service.outbox import repository as outbox_repo

from conftest import claims, login, make_key, register, register_verified, token_for

pytestmark = pytest.mark.needs_pg

FEED_KEY = "feed-key-analytics-0123456789abcdef"
FEED = {"X-Service-Key": FEED_KEY}


async def feed(client, after=0, limit=100, types="", headers=FEED):
    return await client.get("/internal/v1/feed", params={"after": after, "limit": limit, "types": types},
                            headers=headers)


async def admin(client, db, mailer, name="quan_tri"):
    return await token_for(client, db, mailer, username=name, role="admin")


# ── Phiên ───────────────────────────────────────────────────────────────────────

async def test_logout_revokes_the_session_so_the_access_token_dies_immediately(client, db, mailer):
    t = await token_for(client, db, mailer, username="dang_xuat")
    assert (await client.get("/v1/auth/me", headers=t["auth"])).status_code == 200
    await client.post("/v1/auth/logout", json={"refresh_token": t["refresh"]})
    r = await client.get("/v1/auth/me", headers=t["auth"])
    assert (r.status_code, r.json()["detail"]) == (401, "Phiên đã bị thu hồi")
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})).status_code == 401


async def test_other_sessions_of_the_same_user_survive_a_single_logout(client, db, mailer):
    t1 = await token_for(client, db, mailer, username="hai_phien")
    second = (await login(client, "hai_phien")).json()
    await client.post("/v1/auth/logout", json={"refresh_token": t1["refresh"]})
    assert (await client.get("/v1/auth/me", headers=t1["auth"])).status_code == 401
    assert (await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})).status_code == 200


async def test_suspending_an_account_kills_its_sessions_and_refresh_tokens_at_once(client, db, mailer):
    a = await admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="bi_dinh_chi")
    await client.patch(f"/v1/admin/users/{u['id']}/status", headers=a["auth"], json={"status": "suspended"})
    assert (await client.get("/v1/auth/me", headers=u["auth"])).status_code == 401
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": u["refresh"]})).status_code == 401


async def test_password_change_and_reset_revoke_all_sessions(client, db, mailer):
    t = await token_for(client, db, mailer, username="doi_mat_khau")
    await client.post("/v1/auth/forgot-password", json={"email": "doi_mat_khau@example.com"})
    await client.post("/v1/auth/reset-password", json={"token": mailer.last("reset"), "new_password": "mat-khau-moi-999"})
    assert (await client.get("/v1/auth/me", headers=t["auth"])).status_code == 401


async def test_changing_role_forces_a_fresh_token(client, db, mailer):
    a = await admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="duoc_thang_cap")
    await client.patch(f"/v1/admin/users/{u['id']}", headers=a["auth"], json={"role": "dash_admin"})
    assert (await client.get("/v1/auth/me", headers=u["auth"])).status_code == 401  # token cũ mang role cũ
    fresh = (await login(client, "duoc_thang_cap")).json()["access_token"]
    assert (await claims(client, fresh))["role"] == "dash_admin"


async def test_sid_claim_matches_a_live_session_row(client, db, mailer):
    t = await token_for(client, db, mailer, username="sid_hop_le")
    c = await claims(client, t["access"])
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT account_id, revoked_at, client_type FROM sessions WHERE sid=$1", c["sid"])
        assert (row["account_id"], row["revoked_at"], row["client_type"]) == (t["id"], None, "web")
        assert await conn.fetchval("SELECT sid FROM refresh_tokens WHERE account_id=$1", t["id"]) == c["sid"]


async def test_legacy_refresh_token_without_session_gets_one_lazily(client, db, mailer):
    """Refresh token nhập từ legacy chưa có phiên: lần refresh đầu tạo phiên và token mới mang `sid`."""
    t = await token_for(client, db, mailer, username="refresh_cu")
    async with db.connection() as conn:
        await conn.execute("UPDATE refresh_tokens SET sid = NULL WHERE account_id=$1", t["id"])
    r = await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh"]})
    assert r.status_code == 200
    c = await claims(client, r.json()["access_token"])
    assert c["sid"]
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT sid FROM refresh_tokens WHERE account_id=$1", t["id"]) == c["sid"]


# ── Permission ──────────────────────────────────────────────────────────────────

async def test_permission_catalog_is_seeded(client, db, mailer):
    a = await admin(client, db, mailer)
    names = {p["name"]: p for p in (await client.get("/v1/admin/permissions", headers=a["auth"])).json()["permissions"]}
    assert names["reward:access"]["admin_only"] is False and names["reward:admin"]["admin_only"] is True


async def test_grant_permission_shows_up_in_new_tokens_and_revoke_removes_it(client, db, mailer):
    a = await admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="can_quyen")
    r = await client.put(f"/v1/admin/accounts/{u['id']}/permissions/reward:access", headers=a["auth"])
    assert r.json() == {"ok": True, "account_id": u["id"], "permission": "reward:access", "granted": True, "changed": True}
    again = await client.put(f"/v1/admin/accounts/{u['id']}/permissions/reward:access", headers=a["auth"])
    assert again.json()["changed"] is False  # idempotent
    token = (await login(client, "can_quyen")).json()["access_token"]
    assert (await claims(client, token))["perms"] == ["reward:access"]
    assert (await client.delete(f"/v1/admin/accounts/{u['id']}/permissions/reward:access", headers=a["auth"])).json()["changed"]
    token = (await login(client, "can_quyen")).json()["access_token"]
    assert (await claims(client, token))["perms"] == []


async def test_dash_admin_can_grant_normal_but_not_admin_only_permissions(client, db, mailer):
    d = await token_for(client, db, mailer, username="dash_quan_tri", role="dash_admin")
    u = await token_for(client, db, mailer, username="nguoi_nhan")
    assert (await client.put(f"/v1/admin/accounts/{u['id']}/permissions/reward:access", headers=d["auth"])).status_code == 200
    r = await client.put(f"/v1/admin/accounts/{u['id']}/permissions/reward:admin", headers=d["auth"])
    assert (r.status_code, r.json()["detail"]) == (403, "Chỉ quản trị viên (admin) mới được cấp/thu quyền này")


async def test_permission_endpoints_validate_target_and_name(client, db, mailer):
    a = await admin(client, db, mailer)
    assert (await client.put("/v1/admin/accounts/khong-co/permissions/reward:access", headers=a["auth"])).status_code == 404
    u = await token_for(client, db, mailer, username="nguoi_la")
    assert (await client.put(f"/v1/admin/accounts/{u['id']}/permissions/khong:ton_tai", headers=a["auth"])).status_code == 404
    assert (await client.put(f"/v1/admin/accounts/{u['id']}/permissions/reward:access", headers=u["auth"])).status_code == 403
    assert (await client.put(f"/v1/admin/accounts/{u['id']}/permissions/reward:access")).status_code == 401


async def test_viewing_permissions_is_self_or_admin_only(client, db, mailer):
    a = await admin(client, db, mailer)
    u1 = await token_for(client, db, mailer, username="nguoi_mot")
    u2 = await token_for(client, db, mailer, username="nguoi_hai")
    assert (await client.get(f"/v1/accounts/{u1['id']}/permissions", headers=u1["auth"])).status_code == 200
    assert (await client.get(f"/v1/accounts/{u1['id']}/permissions", headers=a["auth"])).status_code == 200
    assert (await client.get(f"/v1/accounts/{u1['id']}/permissions", headers=u2["auth"])).status_code == 403


async def test_legacy_discord_role_implies_reward_access(client, db, mailer):
    await token_for(client, db, mailer, username="vai_tro_discord", role="discord")
    token = (await login(client, "vai_tro_discord")).json()["access_token"]
    assert (await claims(client, token))["perms"] == ["reward:access"]


# ── Outbox & feed ───────────────────────────────────────────────────────────────

async def test_account_lifecycle_emits_events_in_order(client, db, mailer):
    a = await admin(client, db, mailer)
    u = await token_for(client, db, mailer, username="vong_doi_su_kien")
    await client.patch(f"/v1/admin/users/{u['id']}/status", headers=a["auth"], json={"status": "suspended"})
    await client.put(f"/v1/admin/accounts/{u['id']}/permissions/reward:access", headers=a["auth"])
    await client.patch(f"/v1/admin/users/{u['id']}", headers=a["auth"], json={"username": "da_doi_ten", "role": "dash_admin"})
    await client.delete(f"/v1/admin/users/{u['id']}", headers=a["auth"])
    body = (await feed(client, limit=500)).json()
    mine = [e for e in body["events"] if e["data"].get("account_id") == u["id"]]
    assert [e["type"] for e in mine] == [
        "account.registered", "account.verified", "account.status_changed", "session.revoked",
        "account.permission_changed", "account.role_changed", "session.revoked", "account.profile_updated",
        "session.revoked", "account.deleted"]
    seqs = [e["seq"] for e in body["events"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    reg = mine[0]
    assert reg["data"] == {"account_id": u["id"], "username": "vong_doi_su_kien", "role": "viewer", "status": "pending"}
    assert mine[2]["data"] == {"account_id": u["id"], "status": "suspended", "previous": "active"}
    assert mine[4]["data"] == {"account_id": u["id"], "permission": "reward:access", "granted": True}


async def test_events_never_contain_pii_or_secrets(client, db, mailer):
    t = await token_for(client, db, mailer, username="khong_ro_ri")
    await client.post("/v1/auth/forgot-password", json={"email": "khong_ro_ri@example.com"})
    text = (await feed(client, limit=500)).text
    for secret in ("khong_ro_ri@example.com", mailer.last("verify"), mailer.last("reset"), t["refresh"], "password"):
        assert secret not in text


async def test_event_envelope_matches_contract(client, db, mailer):
    await token_for(client, db, mailer, username="dong_goi")
    e = (await feed(client, limit=1)).json()["events"][0]
    assert set(e) == {"seq", "id", "type", "subject", "time", "data_version", "data"}
    assert e["id"].startswith("evt_") and e["time"].endswith("Z") and e["data_version"] == 1
    assert e["subject"].startswith("acc:")


async def test_feed_pagination_cursor_and_type_filter(client, db, mailer):
    for i in range(3):
        await token_for(client, db, mailer, username=f"phan_trang_{i}")
    page1 = (await feed(client, limit=2)).json()
    assert len(page1["events"]) == 2 and page1["has_more"] is True and page1["next_cursor"] == page1["events"][-1]["seq"]
    page2 = (await feed(client, after=page1["next_cursor"], limit=500)).json()
    assert page2["has_more"] is False and page2["events"][0]["seq"] > page1["next_cursor"]
    assert page2["head"] == page2["events"][-1]["seq"]
    only = (await feed(client, types="account.verified", limit=500)).json()
    assert only["events"] and {e["type"] for e in only["events"]} == {"account.verified"}
    empty = (await feed(client, after=page2["head"])).json()
    assert empty["events"] == [] and empty["next_cursor"] == page2["head"] and not empty["has_more"]


@pytest.mark.parametrize("headers", [{}, {"X-Service-Key": ""}, {"X-Service-Key": "sai-khoa-0123456789abcdefghij"},
                                     {"Authorization": "Bearer x"}])
async def test_feed_requires_a_valid_service_key(client, headers):
    r = await client.get("/internal/v1/feed", headers=headers)
    assert r.status_code == 401


async def test_each_reader_has_its_own_key_and_user_tokens_do_not_work(client, db, mailer):
    t = await token_for(client, db, mailer, username="khong_dung_duoc")
    assert (await feed(client, headers={"X-Service-Key": "feed-key-integration-0123456789abc"})).status_code == 200
    assert (await client.get("/internal/v1/feed", headers=t["auth"])).status_code == 401  # token user ≠ service key


async def test_feed_is_disabled_when_no_keys_are_configured(db, settings, mailer, clock, sleeps):
    from dataclasses import replace
    from conftest import _make_client
    async with await _make_client(db, replace(settings, feed_keys=()), mailer, clock, sleeps) as c:
        assert (await c.get("/internal/v1/feed", headers=FEED)).status_code == 401


async def test_event_and_state_change_commit_or_roll_back_together(client, db, mailer, monkeypatch):
    """Transactional outbox: nếu ghi event lỗi thì thay đổi nghiệp vụ cũng không được commit."""
    key = await make_key(db)
    boom = RuntimeError("outbox hỏng giữa chừng")

    async def broken(*a, **kw):
        raise boom

    monkeypatch.setattr(events_mod, "registered", broken)
    with pytest.raises(RuntimeError):
        await register(client, db, username="khong_luu", key=key)
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM accounts WHERE username='khong_luu'") == 0
        assert await conn.fetchval("SELECT used_count FROM invite_keys WHERE key=$1", key) == 0


async def test_failed_transaction_leaves_no_event(client, db):
    async with db.connection() as conn:
        before = await outbox_repo.head(conn)
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await outbox_repo.append(conn, type="account.verified", subject="acc:x", data={"account_id": "x"})
                raise RuntimeError("rollback")
        assert await outbox_repo.head(conn) == before


async def test_cursor_reader_never_loses_events_under_concurrent_writers(db):
    """Nhiều transaction ghi outbox đồng thời với thời gian giữ khác nhau. Người đọc theo con trỏ (như mọi consumer)
    phải thu về ĐỦ MỌI sự kiện. Nếu seq không theo thứ tự commit thì sự kiện có seq nhỏ commit muộn sẽ bị bỏ lỡ vĩnh viễn."""
    collected: list[int] = []
    cursor = 0
    stop = asyncio.Event()

    async def writer(i: int):
        async with db.transaction() as conn:
            await outbox_repo.append(conn, type="account.verified", subject=f"acc:{i}", data={"account_id": str(i)})
            await asyncio.sleep(0.03 * (3 - i % 3))  # seq nhỏ giữ giao dịch lâu hơn -> dễ commit muộn

    async def reader():
        nonlocal cursor
        while not stop.is_set():
            async with db.connection() as conn:
                rows = await outbox_repo.read(conn, after=cursor, limit=500)
            for r in rows:
                collected.append(r["seq"])
            if rows:
                cursor = rows[-1]["seq"]
            await asyncio.sleep(0.003)

    async with db.connection() as conn:
        cursor = await outbox_repo.head(conn)
        start = cursor
    reader_task = asyncio.create_task(reader())
    await asyncio.gather(*[writer(i) for i in range(30)])
    await asyncio.sleep(0.2)  # cho reader quét nốt
    stop.set()
    await reader_task
    async with db.connection() as conn:
        everything = [r["seq"] for r in await outbox_repo.read(conn, after=start, limit=500)]
    assert len(everything) == 30
    assert sorted(collected) == everything, f"consumer bỏ lỡ {sorted(set(everything) - set(collected))}"
    assert collected == sorted(collected)  # và nhận đúng thứ tự
