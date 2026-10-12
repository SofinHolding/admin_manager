"""Channel group (mặt phẳng USER): CRUD nhóm quốc gia + kênh, phạm vi riêng theo owner."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_pg
BASE = "/v1/user/channel-groups"


async def create_group(env, user, **over):
    body = {"name": "Vietnam", "country_code": "VN", **over}
    return await env.http.post(BASE, headers=user["headers"], json=body)


async def test_create_group_ok(env):
    u = env.user()
    r = await create_group(env, u)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["name"] == "Vietnam" and d["country_code"] == "VN" and d["owner_account_id"] == u["sub"]
    assert d["sort_order"] == 0 and "id" in d and "created_at" in d


async def test_list_groups_scoped_to_owner(env):
    a, b = env.user(), env.user()
    assert (await create_group(env, a, name="Vietnam")).status_code == 201
    assert (await create_group(env, b, name="Thailand")).status_code == 201

    ra = await env.http.get(BASE, headers=a["headers"])
    assert ra.status_code == 200
    names_a = [g["name"] for g in ra.json()["data"]]
    assert names_a == ["Vietnam"]

    rb = await env.http.get(BASE, headers=b["headers"])
    names_b = [g["name"] for g in rb.json()["data"]]
    assert names_b == ["Thailand"]


async def test_duplicate_name_409(env):
    u = env.user()
    assert (await create_group(env, u, name="Vietnam")).status_code == 201
    r = await create_group(env, u, name="Vietnam")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"

    # Tên trùng nhưng khác owner thì không bị chặn
    other = env.user()
    assert (await create_group(env, other, name="Vietnam")).status_code == 201


async def test_update_rename(env):
    u = env.user()
    group_id = (await create_group(env, u, name="Vietnam")).json()["data"]["id"]
    r = await env.http.patch(f"{BASE}/{group_id}", headers=u["headers"], json={"name": "Viet Nam"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["name"] == "Viet Nam" and d["id"] == group_id and d["country_code"] == "VN"

    # Người khác không sửa được nhóm không phải của mình
    other = env.user()
    r2 = await env.http.patch(f"{BASE}/{group_id}", headers=other["headers"], json={"name": "Hacked"})
    assert r2.status_code == 404 and r2.json()["error"]["code"] == "NOT_FOUND"


async def test_delete_group(env):
    u = env.user()
    group_id = (await create_group(env, u, name="Vietnam")).json()["data"]["id"]
    r = await env.http.delete(f"{BASE}/{group_id}", headers=u["headers"])
    assert r.status_code == 200 and r.json()["data"]["deleted"] is True

    listing = (await env.http.get(BASE, headers=u["headers"])).json()["data"]
    assert listing == []

    r2 = await env.http.delete(f"{BASE}/{group_id}", headers=u["headers"])
    assert r2.status_code == 404 and r2.json()["error"]["code"] == "NOT_FOUND"


async def test_unauthenticated_401(env):
    assert (await env.http.get(BASE)).status_code == 401
    assert (await env.http.post(BASE, json={"name": "Vietnam"})).status_code == 401
    assert (await env.http.patch(f"{BASE}/00000000-0000-0000-0000-000000000000", json={"name": "x"})).status_code == 401
    assert (await env.http.delete(f"{BASE}/00000000-0000-0000-0000-000000000000")).status_code == 401
