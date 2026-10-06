"""`GET /v1/analytics/platforms`: danh mục nền tảng CÓ dữ liệu (để giao diện không hardcode YouTube/Facebook)."""

import pytest

from analytics_testsupport import ADMIN, OWN_A, OWN_B, post, res

pytestmark = pytest.mark.needs_pg


async def seed(world):
    world.integration.commit(owner=OWN_A, platform="demo_social",
                             resources=[res("c1", "A", platform="demo_social", country="Brazil", managed=True, active=True)],
                             content=[post("p1", "c1", "2026-10-01", 5, platform="demo_social")])
    world.integration.commit(owner=OWN_B, platform="toolx_social",
                             resources=[res("c2", "B", platform="toolx_social", country="Chile", managed=True, active=True)],
                             content=[post("p2", "c2", "2026-10-01", 7, platform="toolx_social")])
    await world.sync()


async def test_viewer_sees_only_platforms_of_own_data_and_admin_sees_all(world):
    await seed(world)
    mine = await world.client.get("/v1/analytics/platforms", headers=world.as_user(OWN_A))
    assert mine.status_code == 200 and mine.json()["items"] == ["demo_social"]
    admin = await world.client.get("/v1/analytics/platforms", headers=world.as_user(ADMIN, role="admin"))
    assert admin.json()["items"] == ["demo_social", "toolx_social"]


async def test_catalog_is_not_narrowed_by_the_selected_platform_and_filters_by_it_work(world):
    await seed(world)
    h = world.as_user(ADMIN, role="admin")
    narrowed = await world.client.get("/v1/analytics/platforms", headers=h, params={"platform": "demo_social"})
    assert narrowed.json()["items"] == ["demo_social", "toolx_social"]       # danh mục không co theo ô đang chọn
    s = await world.client.get("/v1/analytics/summary", headers=h, params={"platform": "toolx_social",
                                                                         "date_from": "2026-10-01", "date_to": "2026-10-03"})
    assert s.json()["total_views"] == 7                                        # lọc theo nền tảng của Tool mới hoạt động


async def test_requires_authentication(world):
    assert (await world.client.get("/v1/analytics/platforms")).status_code == 401
