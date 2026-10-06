"""Khoá chính read model khớp khoá tự nhiên của Integration (migration 0002).

Integration: events UNIQUE(resource_id, external_id), resources UNIQUE(owner, platform, resource_type, external_id).
Trước 0002 Analytics khoá events theo (owner, external_id) và resources theo (owner, platform, external_id) nên bản ghi hợp lệ
ở nguồn bị ghi đè lẫn nhau trong read model (mất dữ liệu im lặng)."""

import pytest

from analytics_testsupport import OWN_A, event, post, res

pytestmark = pytest.mark.needs_pg

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}


async def test_same_event_external_id_on_two_channels_both_kept(world):
    """Hai kênh cùng owner dùng event external_id 'e1': cả hai tồn tại và cùng được đếm."""
    world.integration.commit(
        owner=OWN_A, platform="facebook",
        resources=[res("pgx", "X", platform="facebook", managed=True, active=True),
                   res("pgy", "Y", platform="facebook", managed=True, active=True)],
        events=[event("e1", "pgx", "2026-10-01"), event("e1", "pgy", "2026-10-02")])
    await world.sync()
    rows = await world.fetch("SELECT resource_external_id FROM read_events WHERE external_id = 'e1' ORDER BY 1")
    assert [r["resource_external_id"] for r in rows] == ["pgx", "pgy"]
    body = (await world.get("/v1/analytics/summary", **RANGE)).json()
    assert body["videos_posted"] == 2


async def test_two_resource_types_same_external_id_both_kept(world):
    world.integration.commit(
        owner=OWN_A, platform="facebook",
        resources=[res("dup", "As Page", platform="facebook", resource_type="page"),
                   res("dup", "As Profile", platform="facebook", resource_type="profile")])
    await world.sync()
    rows = await world.fetch("SELECT resource_type FROM read_resources WHERE external_id = 'dup' ORDER BY 1")
    assert [r["resource_type"] for r in rows] == ["page", "profile"]
    summary = await world.consumer.reconcile()
    assert summary["kinds"]["resources"]["count_match"] and summary["ok"]


async def test_content_and_metrics_keyed_by_resource_type(world):
    """content/metrics cùng external_id & resource_external_id nhưng khác resource_type không ghi đè nhau."""
    world.integration.commit(
        owner=OWN_A, platform="facebook",
        resources=[res("dup", "P", platform="facebook", resource_type="page"),
                   res("dup", "Q", platform="facebook", resource_type="profile")],
        content=[{**post("c1", "dup", "2026-10-01", 10), "resource_type": "page"},
                 {**post("c1", "dup", "2026-10-01", 20), "resource_type": "profile"}])
    await world.sync()
    rows = await world.fetch("SELECT resource_type, views FROM read_content ORDER BY resource_type")
    assert [(r["resource_type"], r["views"]) for r in rows] == [("page", 10), ("profile", 20)]


async def test_removed_type_does_not_hide_live_type_with_same_external_id(world):
    """Một resource_type bị gỡ không được che số liệu của resource_type còn sống cùng external_id."""
    world.integration.commit(
        owner=OWN_A, platform="facebook",
        resources=[res("dup", "P", platform="facebook", resource_type="page", managed=True, active=True),
                   res("dup", "Q", platform="facebook", resource_type="profile", managed=True, active=True)],
        content=[post("c1", "dup", "2026-10-01", 10)])
    world.integration.remove("resources", external_id="dup", resource_type="page")
    await world.sync()
    body = (await world.get("/v1/analytics/summary", **RANGE)).json()
    assert body["total_views"] == 10
