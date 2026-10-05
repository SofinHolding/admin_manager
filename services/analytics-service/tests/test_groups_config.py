"""Nhóm kênh (CRUD theo chủ sở hữu) và cấu hình quốc gia (alias / tên chuẩn)."""

import pytest

from analytics_testsupport import ADMIN, OWN_A, OWN_B

pytestmark = pytest.mark.needs_pg


async def mk(w, sub=OWN_A, name="G", prefix="/v1/analytics"):
    r = await w.client.post(prefix + "/groups", json={"name": name}, headers=w.as_user(sub))
    assert r.status_code == 200, r.text
    return r.json()


# ── Nhóm kênh ───────────────────────────────────────────────────────────────────

async def test_group_crud_and_member_upsert(world):
    c, h = world.client, world.as_user(OWN_A)
    g = await mk(world, name="  Nhóm A  ")
    assert g["name"] == "Nhóm A" and g["account_id"] == OWN_A
    base = f"/v1/analytics/groups/{g['id']}"
    m1 = await c.post(base + "/members", json={"channel_ref": "pg1", "platform_tag": "x"}, headers=h)
    m2 = await c.post(base + "/members", json={"channel_ref": "pg1", "platform_tag": "y", "country_override": "Chile"},
                      headers=h)
    assert m1.status_code == m2.status_code == 200
    listing = (await c.get("/v1/analytics/groups", headers=h)).json()["items"]
    assert len(listing) == 1 and listing[0]["members"] == [{"channel_ref": "pg1", "platform_tag": "y",
                                                            "country_override": "Chile"}]     # upsert, không nhân đôi
    assert (await c.patch(base, json={"name": "Đổi tên"}, headers=h)).json() == {"ok": True}
    assert (await c.get("/v1/analytics/groups", headers=h)).json()["items"][0]["name"] == "Đổi tên"
    assert (await c.delete(base + "/members/pg1", headers=h)).json() == {"ok": True}
    assert (await c.delete(base + "/members/pg1", headers=h)).status_code == 404
    assert (await c.delete(base, headers=h)).json() == {"ok": True}
    assert (await c.get("/v1/analytics/groups", headers=h)).json() == {"items": []}


async def test_deleting_group_removes_its_members(world):
    g = await mk(world)
    await world.client.post(f"/v1/analytics/groups/{g['id']}/members", json={"channel_ref": "pg1"},
                            headers=world.as_user(OWN_A))
    await world.client.delete(f"/v1/analytics/groups/{g['id']}", headers=world.as_user(OWN_A))
    assert (await world.fetch("SELECT count(*) AS n FROM channel_group_members"))[0]["n"] == 0


async def test_groups_are_owned_admin_may_view_but_not_edit(world):
    g = await mk(world, sub=OWN_A, name="của A")
    base = f"/v1/analytics/groups/{g['id']}"
    hb, ha = world.as_user(OWN_B), world.as_user(ADMIN, "admin")
    c = world.client
    for method, kw in (("PATCH", {"json": {"name": "x"}}), ("DELETE", {}), ("POST", {"json": {"channel_ref": "a"}})):
        url = base + ("/members" if method == "POST" else "")
        for headers in (hb, ha):
            r = await c.request(method, url, headers=headers, **kw)
            assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED", (method, headers)
    assert (await c.delete(base + "/members/a", headers=hb)).status_code == 403
    # viewer B chỉ thấy nhóm của mình, kể cả khi truyền owner_account_id của A
    assert (await c.get("/v1/analytics/groups", params={"owner_account_id": OWN_A}, headers=hb)).json() == {"items": []}
    # admin xem được nhóm của A khi chỉ định owner
    seen = (await c.get("/v1/analytics/groups", params={"owner_account_id": OWN_A}, headers=ha)).json()["items"]
    assert [x["id"] for x in seen] == [g["id"]]
    # nhóm không tồn tại -> 404; kiểu lỗi legacy ở alias
    nf = await c.patch("/v1/viewer/groups/khong-co", json={"name": "x"}, headers=hb)
    assert nf.status_code == 404 and nf.json() == {"detail": "Không tìm thấy group"}
    forbidden = await c.patch(f"/v1/viewer/groups/{g['id']}", json={"name": "x"}, headers=hb)
    assert forbidden.status_code == 403 and forbidden.json() == {"detail": "Không có quyền sửa group này"}
    assert (await c.get("/v1/analytics/groups", headers=world.as_user(OWN_A))).json()["items"][0]["name"] == "của A"


async def test_group_input_validation(world):
    c, h = world.client, world.as_user(OWN_A)
    for bad in ({"name": ""}, {"name": "x" * 101}, {}, {"name": None}):
        r = await c.post("/v1/analytics/groups", json=bad, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    g = await mk(world)
    for bad in ({"channel_ref": ""}, {"channel_ref": "x" * 201}, {"channel_ref": "a", "platform_tag": "t" * 51},
                {"channel_ref": "a", "country_override": "c" * 51}):
        assert (await c.post(f"/v1/analytics/groups/{g['id']}/members", json=bad, headers=h)).status_code == 422
    nul = await c.post(f"/v1/analytics/groups/{g['id']}/members", json={"channel_ref": "a\u0000b"}, headers=h)
    assert nul.status_code == 422 and nul.json()["ok"] is False        # ký tự NUL: lỗi người gọi, không phải 500
    assert (await c.post("/v1/analytics/groups", content=b"{", headers={**h, "Content-Type": "application/json"})
            ).status_code == 422


async def test_group_edits_bump_seal(world):
    s0 = await world.seal()
    g = await mk(world)
    s1 = await world.seal()
    await world.client.post(f"/v1/analytics/groups/{g['id']}/members", json={"channel_ref": "a"},
                            headers=world.as_user(OWN_A))
    s2 = await world.seal()
    await world.client.patch(f"/v1/analytics/groups/{g['id']}", json={"name": "n"}, headers=world.as_user(OWN_A))
    s3 = await world.seal()
    await world.client.delete(f"/v1/analytics/groups/{g['id']}", headers=world.as_user(OWN_A))
    s4 = await world.seal()
    assert s0 < s1 < s2 < s3 < s4


async def test_failed_group_write_does_not_bump_seal(world):
    s0 = await world.seal()
    r = await world.client.post("/v1/analytics/groups/khong-co/members", json={"channel_ref": "a"},
                                headers=world.as_user(OWN_A))
    assert r.status_code == 404 and await world.seal() == s0


# ── Cấu hình quốc gia ───────────────────────────────────────────────────────────

async def test_country_canonical_and_alias_lifecycle(world):
    c, a = world.client, world.as_user(ADMIN, "dash_admin")
    base = "/v1/analytics/admin"
    assert (await c.post(base + "/country-aliases", json={"alias": "BR", "canonical": "Brazil"}, headers=a)
            ).status_code == 400                                         # tên chuẩn phải tồn tại trước
    assert (await c.post(base + "/country-canonicals", json={"name": " Brazil "}, headers=a)).json()["name"] == "Brazil"
    dup = await c.post(base + "/country-canonicals", json={"name": "BRAZIL"}, headers=a)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "RESOURCE_CONFLICT"
    up = await c.post(base + "/country-aliases", json={"alias": " Brasil ", "canonical": "brazil"}, headers=a)
    assert up.json() == {"alias": "brasil", "canonical": "Brazil"}         # alias lower-case, canonical đúng chữ đã lưu
    await c.post(base + "/country-aliases", json={"alias": "BR", "canonical": "Brazil"}, headers=a)
    items = (await c.get(base + "/country-aliases", headers=a)).json()["items"]
    assert [i["alias"] for i in items] == ["br", "brasil"]
    in_use = await c.delete(base + "/country-canonicals/Brazil", headers=a)
    assert in_use.status_code == 409 and "2 biến thể" in in_use.json()["error"]["message"]
    assert (await c.patch(base + "/country-canonicals/brazil", json={"name": "Brasil"}, headers=a)).json() == {
        "ok": True, "name": "Brasil"}
    assert {i["canonical"] for i in (await c.get(base + "/country-aliases", headers=a)).json()["items"]} == {"Brasil"}
    assert (await c.patch(base + "/country-canonicals/khong-co", json={"name": "x"}, headers=a)).status_code == 404
    await c.post(base + "/country-canonicals", json={"name": "Peru"}, headers=a)
    conflict = await c.patch(base + "/country-canonicals/Peru", json={"name": "brasil"}, headers=a)
    assert conflict.status_code == 409
    assert (await c.delete(base + "/country-aliases/BR", headers=a)).json() == {"ok": True}      # không phân biệt hoa/thường
    assert (await c.delete(base + "/country-aliases/br", headers=a)).status_code == 404
    assert (await c.delete(base + "/country-aliases/brasil", headers=a)).status_code == 200
    assert (await c.delete(base + "/country-canonicals/Brasil", headers=a)).json() == {"ok": True}
    assert (await c.delete(base + "/country-canonicals/Brasil", headers=a)).status_code == 404


async def test_legacy_country_errors_use_detail_shape(world):
    c, a = world.client, world.as_user(ADMIN, "admin")
    r = await c.post("/v1/admin/country-aliases", json={"alias": "x", "canonical": "chưa-có"}, headers=a)
    assert r.status_code == 400 and set(r.json()) == {"detail"}
    r = await c.delete("/v1/admin/country-aliases/khong-co", headers=a)
    assert r.status_code == 404 and r.json() == {"detail": "Không tìm thấy alias"}


async def test_country_config_writes_bump_seal_failed_writes_do_not(world):
    a = world.as_user(ADMIN, "admin")
    s0 = await world.seal()
    await world.client.post("/v1/analytics/admin/country-canonicals", json={"name": "Peru"}, headers=a)
    s1 = await world.seal()
    await world.client.post("/v1/analytics/admin/country-canonicals", json={"name": "Peru"}, headers=a)     # 409
    assert s1 > s0 and await world.seal() == s1
