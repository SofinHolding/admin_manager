"""Test cho Phase A — `resources.py` (User API) + `ops.py` (Ops API) + `auth.py` client_type.

Bám tiêu chí nghiệm thu mục 27 `tasks/backlog/local-operations-hub-v1.md`:
1/2  viewer tạo Source + nhập resource trên website → origin=web_manual, mapping_status=unmapped.
3    admin/dash_admin login Tool B (X-Client-Type) bằng account hiện có; viewer/discord bị chặn.
4    Ops inventory hiển thị rõ owner (username) cho mọi resource.
5    Không hardcode ITL/Sible — tạo Source tuỳ ý (ở đây đặt tên khác đi) vẫn hoạt động.
6/7  Filter + multi-select tạo batch được.
8/9  Resource execution_mode != fallback_local, hoặc platform tắt fallback → KHÔNG được queue.
10   V1 chưa có adapter → item luôn dừng ở blocked_no_adapter, KHÔNG giả lập success.
15   Thay đổi execution_mode/policy/batch có ghi audit log.
"""

from __future__ import annotations

import sqlite3

from conftest import _TEST_DB, auth_headers, login


async def test_viewer_tao_source_va_resource_web_manual(client, make_account):
    viewer = await make_account("viewer")
    r = login(client, viewer["username"], viewer["password"])
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    r = client.post("/v1/resources/sources", json={
        "name": "Facebook Test Project", "platform": "facebook",
        "project_key": "TESTPROJ", "default_country": "Brazil",
    }, headers=auth_headers(token))
    assert r.status_code == 200, r.text
    source = r.json()
    assert source["platform"] == "facebook"

    r = client.post("/v1/resources", json={
        "source_instance_id": source["id"], "platform": "facebook",
        "resource_type": "page", "input_ref": "https://facebook.com/test.page.123",
        "display_name": "Test Page 123", "country": "Brazil",
    }, headers=auth_headers(token))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["origin"] == "web_manual"
    assert res["mapping_status"] == "unmapped"
    assert res["verification_status"] == "unverified"
    # facebook seed mặc định fallback_enabled=True → execution_mode phải là fallback_local, KHÔNG
    # phải hardcode "disabled".
    assert res["execution_mode"] == "fallback_local"

    r = client.get("/v1/resources", params={"platform": "facebook"}, headers=auth_headers(token))
    assert r.status_code == 200
    assert any(it["id"] == res["id"] for it in r.json()["items"])


async def test_platform_chua_bat_web_entry_bi_chan(client, make_account):
    viewer = await make_account("viewer")
    token = login(client, viewer["username"], viewer["password"]).json()["access_token"]
    # 'snapchat' không nằm trong policy seed mặc định → web_entry chưa bật → 400.
    r = client.post("/v1/resources/sources", json={
        "name": "Chưa hỗ trợ", "platform": "snapchat",
    }, headers=auth_headers(token))
    assert r.status_code == 400


async def test_tool_b_login_chi_cho_admin_dash_admin(client, make_account):
    viewer = await make_account("viewer")
    admin = await make_account("admin")
    dash = await make_account("dash_admin")

    r = login(client, viewer["username"], viewer["password"], client_type="local_fallback_tool")
    assert r.status_code == 403, r.text

    r = login(client, admin["username"], admin["password"], client_type="local_fallback_tool")
    assert r.status_code == 200, r.text
    payload_role = _decode_role(r.json()["access_token"])
    assert payload_role == "admin"

    r = login(client, dash["username"], dash["password"], client_type="local_fallback_tool")
    assert r.status_code == 200, r.text

    # Audit log phải ghi nhận đúng 2 lần login_fallback_tool (admin + dash_admin), KHÔNG ghi cho
    # lần viewer bị chặn.
    conn = sqlite3.connect(_TEST_DB)
    rows = conn.execute(
        "SELECT account_id FROM operation_audit_log WHERE event_type = 'login_fallback_tool' "
        "AND account_id IN (?, ?)", (admin["id"], dash["id"])).fetchall()
    conn.close()
    assert len(rows) == 2


def _decode_role(token: str) -> str:
    import jwt
    return jwt.decode(token, options={"verify_signature": False}).get("role")


async def test_ops_inventory_hien_thi_ro_owner_va_khong_hardcode_source(client, make_account):
    viewer = await make_account("viewer")
    admin = await make_account("admin")
    vtoken = login(client, viewer["username"], viewer["password"]).json()["access_token"]
    atoken = login(client, admin["username"], admin["password"]).json()["access_token"]

    # Tên Source tuỳ ý — KHÔNG phải ITL/Sible — để chứng minh không hardcode (tiêu chí #5).
    for name in ("Chiến dịch Mùa Hè", "Du An Thu Ba"):
        s = client.post("/v1/resources/sources", json={
            "name": name, "platform": "tiktok",
        }, headers=auth_headers(vtoken)).json()
        client.post("/v1/resources", json={
            "source_instance_id": s["id"], "platform": "tiktok",
            "input_ref": f"@{name.replace(' ', '').lower()}",
        }, headers=auth_headers(vtoken))

    # viewer thường KHÔNG được gọi /v1/ops/*
    r = client.get("/v1/ops/inventory", headers=auth_headers(vtoken))
    assert r.status_code == 403

    r = client.get("/v1/ops/inventory", params={"platform": "tiktok"}, headers=auth_headers(atoken))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    mine = [it for it in items if it["owner_account_id"] == viewer["id"]]
    assert len(mine) == 2
    assert all(it["owner_username"] == viewer["username"] for it in mine)

    r = client.get("/v1/ops/sources", headers=auth_headers(atoken))
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["items"]}
    assert {"Chiến dịch Mùa Hè", "Du An Thu Ba"} <= names


async def test_run_queue_khong_gia_lap_success_va_tu_choi_dung_dieu_kien(client, make_account):
    viewer = await make_account("viewer")
    admin = await make_account("admin")
    vtoken = login(client, viewer["username"], viewer["password"]).json()["access_token"]
    atoken = login(client, admin["username"], admin["password"]).json()["access_token"]

    # 1 resource facebook hợp lệ (fallback_local, platform bật fallback).
    s = client.post("/v1/resources/sources", json={
        "name": "Queue Test", "platform": "facebook",
    }, headers=auth_headers(vtoken)).json()
    ok_res = client.post("/v1/resources", json={
        "source_instance_id": s["id"], "platform": "facebook", "input_ref": "https://fb.com/ok",
    }, headers=auth_headers(vtoken)).json()

    # 1 resource instagram — policy seed mặc định fallback_enabled=False → phải bị skip.
    s2 = client.post("/v1/resources/sources", json={
        "name": "IG Test", "platform": "instagram",
    }, headers=auth_headers(vtoken)).json()
    ig_res = client.post("/v1/resources", json={
        "source_instance_id": s2["id"], "platform": "instagram", "input_ref": "https://ig.com/x",
    }, headers=auth_headers(vtoken)).json()
    assert ig_res["execution_mode"] == "disabled"  # platform chưa bật fallback

    batch = client.post("/v1/ops/batches", json={"platform": "facebook", "name": "Batch 1"},
                        headers=auth_headers(atoken)).json()

    r = client.post(f"/v1/ops/batches/{batch['id']}/items",
                    json={"resource_ids": [ok_res["id"], ig_res["id"]]},
                    headers=auth_headers(atoken))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["added"]) == 1
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["resource_id"] == ig_res["id"]
    assert body["skipped"][0]["reason"] in ("execution_mode_not_fallback_local", "platform_mismatch")

    items = client.get(f"/v1/ops/batches/{batch['id']}", headers=auth_headers(atoken)).json()["items"]
    assert len(items) == 1
    # V1 chưa có adapter (adapter_status seed = 'none') → KHÔNG được giả lập success.
    assert items[0]["status"] == "blocked_no_adapter"
    assert items[0]["status"] != "success"


async def test_chi_admin_duoc_sua_platform_policy_va_execution_mode(client, make_account):
    viewer = await make_account("viewer")
    dash = await make_account("dash_admin")
    admin = await make_account("admin")
    vtoken = login(client, viewer["username"], viewer["password"]).json()["access_token"]
    dtoken = login(client, dash["username"], dash["password"]).json()["access_token"]
    atoken = login(client, admin["username"], admin["password"]).json()["access_token"]

    # dash_admin đọc được nhưng không sửa được policy.
    r = client.get("/v1/ops/platform-policies", headers=auth_headers(dtoken))
    assert r.status_code == 200
    r = client.patch("/v1/ops/platform-policies/instagram",
                     json={"fallback_enabled": True}, headers=auth_headers(dtoken))
    assert r.status_code == 403
    r = client.patch("/v1/ops/platform-policies/instagram",
                     json={"fallback_enabled": True}, headers=auth_headers(vtoken))
    assert r.status_code == 403

    # admin sửa được, và sau khi bật, resource instagram tạo MỚI phải lên fallback_local.
    r = client.patch("/v1/ops/platform-policies/instagram",
                     json={"fallback_enabled": True}, headers=auth_headers(atoken))
    assert r.status_code == 200, r.text
    assert r.json()["fallback_enabled"] is True

    s = client.post("/v1/resources/sources", json={
        "name": "IG Now Enabled", "platform": "instagram",
    }, headers=auth_headers(vtoken)).json()
    res = client.post("/v1/resources", json={
        "source_instance_id": s["id"], "platform": "instagram", "input_ref": "https://ig.com/y",
    }, headers=auth_headers(vtoken)).json()
    assert res["execution_mode"] == "fallback_local"

    # Audit log ghi nhận platform_policy_changed.
    conn = sqlite3.connect(_TEST_DB)
    rows = conn.execute(
        "SELECT COUNT(*) FROM operation_audit_log WHERE event_type = 'platform_policy_changed' "
        "AND target_id = 'instagram'").fetchone()
    conn.close()
    assert rows[0] >= 1

    # Viewer KHÔNG được tự đổi execution_mode qua route của mình — field không nằm trong
    # `UpdateResourceBody` nên Pydantic lờ đi, không còn gì để cập nhật → 400.
    r = client.patch(f"/v1/resources/{res['id']}", json={"execution_mode": "primary_tool"},
                     headers=auth_headers(vtoken))
    assert r.status_code == 400
    unchanged = client.get("/v1/resources", params={"platform": "instagram"},
                           headers=auth_headers(vtoken)).json()["items"]
    assert next(it for it in unchanged if it["id"] == res["id"])["execution_mode"] == "fallback_local"

    # Chỉ admin đổi được qua /v1/ops/resources/{id}/execution-mode.
    r = client.patch(f"/v1/ops/resources/{res['id']}/execution-mode",
                     json={"execution_mode": "disabled"}, headers=auth_headers(dtoken))
    assert r.status_code == 403
    r = client.patch(f"/v1/ops/resources/{res['id']}/execution-mode",
                     json={"execution_mode": "disabled"}, headers=auth_headers(atoken))
    assert r.status_code == 200, r.text
