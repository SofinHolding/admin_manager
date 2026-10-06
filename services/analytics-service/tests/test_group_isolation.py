"""Nhóm kênh / override quốc gia của owner A không được tác động lên kênh cùng external_id của owner B.

`channel_ref` chỉ là external_id nên hai owner có thể trùng; khi admin xem toàn bộ (owner=None) dataset gồm kênh của nhiều
owner. Trước đây override/nhóm khoá theo channel_ref nên nhóm của A đổi quốc gia, lọc và so sánh cả kênh của B."""

import pytest

from analytics_testsupport import ADMIN, OWN_A, OWN_B, post, res

pytestmark = pytest.mark.needs_pg

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}
ADMIN_H = {"sub": ADMIN, "role": "admin"}


async def two_owners_same_external_id(world):
    """A và B đều có kênh 'pg1' (facebook). Quốc gia gốc: A=Brazil, B=Chile."""
    world.integration.commit(
        owner=OWN_A, platform="facebook",
        resources=[res("pg1", "A page", platform="facebook", country="Brazil", managed=True, active=True)],
        content=[post("a1", "pg1", "2026-10-01", 100)])
    world.integration.commit(
        owner=OWN_B, platform="facebook",
        resources=[res("pg1", "B page", platform="facebook", country="Chile", managed=True, active=True)],
        content=[post("b1", "pg1", "2026-10-01", 7)])
    await world.sync()


async def make_group(world, sub, ref="pg1", **member):
    g = (await world.client.post("/v1/analytics/groups", json={"name": "G-" + sub[:1]},
                                 headers=world.as_user(sub))).json()
    r = await world.client.post(f"/v1/analytics/groups/{g['id']}/members", json={"channel_ref": ref, **member},
                                headers=world.as_user(sub))
    assert r.status_code == 200, r.text
    return g


def by_country(body) -> dict:
    out: dict = {}
    for day in body["days"]:
        for k, v in day.items():
            if k != "date" and v is not None:
                out[k] = out.get(k, 0) + v
    return out


async def test_country_override_of_owner_a_does_not_touch_owner_b_entity(world):
    await two_owners_same_external_id(world)
    await make_group(world, OWN_A, country_override="Peru")
    admin_view = (await world.get("/v1/analytics/views-by-country", **ADMIN_H, **RANGE)).json()
    assert by_country(admin_view) == {"Peru": 100, "Chile": 7}          # B giữ Chile (trước đây bị đổi thành Peru: 107)
    own_b = (await world.get("/v1/analytics/views-by-country", sub=OWN_B, **RANGE)).json()
    assert by_country(own_b) == {"Chile": 7}
    own_a = (await world.get("/v1/analytics/views-by-country", sub=OWN_A, **RANGE)).json()
    assert by_country(own_a) == {"Peru": 100}                            # override vẫn tác dụng với CHÍNH A


async def test_group_filter_and_group_compare_only_cover_the_groups_owner(world):
    await two_owners_same_external_id(world)
    g = await make_group(world, OWN_A)
    s = (await world.get("/v1/analytics/summary", **ADMIN_H, group_id=g["id"], **RANGE)).json()
    assert s["total_views"] == 100                                       # chỉ kênh pg1 của A (trước đây 107)
    cmp_ = (await world.get("/v1/analytics/compare/group", **ADMIN_H, **RANGE)).json()
    assert [(i["id"], i["total"]) for i in cmp_["items"]] == [(g["id"], 100)]


async def test_platform_tag_filter_does_not_cross_owners(world):
    await two_owners_same_external_id(world)
    await make_group(world, OWN_A, platform_tag="my-tag")
    s = (await world.get("/v1/analytics/summary", **ADMIN_H, platform_tag="my-tag", **RANGE)).json()
    assert s["total_views"] == 100
    bd = (await world.get("/v1/analytics/channel-breakdown", **ADMIN_H, **RANGE)).json()
    tag_rows = [r for r in bd["by_platform"] if r.get("is_custom_tag")]
    assert tag_rows == [{"platform": "my-tag", "managed": 1, "active": 1, "is_custom_tag": True}]


async def test_admin_group_is_a_management_group_and_applies_across_owners(world):
    """Nhóm do admin tạo (role theo account_projection) là nhóm quản trị: vẫn áp lên mọi owner như hành vi cũ."""
    await two_owners_same_external_id(world)
    world.identity_feed.account(ADMIN, "account.registered", role="admin")
    await world.sync()
    g = await make_group(world, ADMIN, country_override="Peru")
    admin_view = (await world.get("/v1/analytics/views-by-country", **ADMIN_H, **RANGE)).json()
    assert by_country(admin_view) == {"Peru": 107}
    s = (await world.get("/v1/analytics/summary", **ADMIN_H, group_id=g["id"], **RANGE)).json()
    assert s["total_views"] == 107
