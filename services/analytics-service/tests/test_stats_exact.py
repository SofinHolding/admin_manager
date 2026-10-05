"""Số liệu CHÍNH XÁC trên fixture nhiều Tool/nền tảng. Mọi con số kỳ vọng được TÍNH TAY từ `seed_world`
(analytics_testsupport.py), không sinh bằng chính code đang test.

Fixture (owner A, ngày 2026-10-01..03; B là người khác):
  Facebook: pg1(Brazil; ITL+Sible) p1=100/10/1 & p2=50/5/2 (10-01), p3=200/20/4 (10-02), p7=11 (09-30);
            pg2(quốc gia trống -> Brazil từ project ITL) p4=30/3/0 (10-02), p6=null (10-03);
            pg3(Peru; Sible nhưng liên kết TẮT) p5=70/7/7 (10-03)
  YouTube : ch1(Peru) 10-01=40, 10-02=0 ; ch2(quốc gia trống) 10-01=60, 10-03=10
  Sự kiện post_ok: e1(10-01) e2(10-02) e3(10-02) e4(10-03) e5(10-01) | post_fail e6 | trước kỳ: e7(09-30)
"""

import pytest

from analytics_testsupport import ADMIN, OWN_A, OWN_B

pytestmark = pytest.mark.needs_pg

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}


async def test_summary_is_exact(seeded):
    r = await seeded.get("/v1/analytics/summary", **RANGE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {k: body[k] for k in ("total_views", "total_views_prev", "videos_posted", "videos_posted_prev",
                                 "active_channels", "managed_channels", "days_in_range")} == {
        "total_views": 560, "total_views_prev": 11, "videos_posted": 5, "videos_posted_prev": 1,
        "active_channels": 4, "managed_channels": 5, "days_in_range": 3}
    assert body["linked"] is True


async def test_traffic_is_exact_and_null_is_not_zero(seeded):
    body = (await seeded.get("/v1/analytics/traffic", **RANGE)).json()
    assert body["days"] == [
        {"date": "2026-10-01", "views": 250, "interactions": 15, "comments": 3, "videos": 2},
        {"date": "2026-10-02", "views": 230, "interactions": 23, "comments": 4, "videos": 2},
        {"date": "2026-10-03", "views": 80, "interactions": 7, "comments": 7, "videos": 2}]
    assert body["totals"] == {"views": 560, "interactions": 45, "comments": 14, "videos": 6}
    assert body["scope"] == "" and body["scope_label"] == "Tổng — cả hai nền tảng"
    # Ngày CHƯA có dữ liệu = null, KHÁC 0 (ngày 09-29 không có gì; 10-02 của ch1 có dữ liệu và bằng 0)
    empty = (await seeded.get("/v1/analytics/traffic", date_from="2026-09-28", date_to="2026-09-30")).json()
    assert [d["views"] for d in empty["days"]] == [None, None, 11]


async def test_traffic_metric_param_returns_single_series(seeded):
    body = (await seeded.get("/v1/analytics/traffic", metric="interactions", **RANGE)).json()
    assert body["metric"] == "interactions" and [p["value"] for p in body["series"]] == [15, 23, 7]
    bad = await seeded.get("/v1/analytics/traffic", metric="khong-co", **RANGE)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


async def test_top_channels_is_exact(seeded):
    items = (await seeded.get("/v1/analytics/top-channels", **RANGE)).json()["items"]
    assert items == [
        {"id": "pg1", "name": "Page One", "platform": "facebook", "country": "Brazil", "views": 350},
        {"id": "youtube:ch2", "name": "Chan Two", "platform": "youtube", "country": "", "views": 70},
        {"id": "pg3", "name": "Page Three", "platform": "facebook", "country": "Peru", "views": 70},
        {"id": "youtube:ch1", "name": "Chan One", "platform": "youtube", "country": "Peru", "views": 40},
        {"id": "pg2", "name": "Page Two", "platform": "facebook", "country": "Brazil", "views": 30}]
    top2 = (await seeded.get("/v1/analytics/top-channels", limit=2, **RANGE)).json()["items"]
    assert [i["id"] for i in top2] == ["pg1", "youtube:ch2"]


async def test_views_by_country_is_exact(seeded):
    body = (await seeded.get("/v1/analytics/views-by-country", **RANGE)).json()
    assert body["countries"] == ["Brazil", "Khác", "Peru"]
    assert body["days"] == [
        {"date": "2026-10-01", "Brazil": 150, "Peru": 40, "Khác": 60},
        {"date": "2026-10-02", "Brazil": 230, "Peru": 0},
        {"date": "2026-10-03", "Brazil": 0, "Peru": 70, "Khác": 10}]
    assert body["totals"] == {"Brazil": 380, "Peru": 110, "Khác": 70}
    assert sum(body["totals"].values()) == 560


async def test_country_tags_and_metrics_catalogue(seeded):
    tags = (await seeded.get("/v1/analytics/country-tags")).json()
    assert tags["items"] == [{"id": "Brazil", "name": "Brazil"}, {"id": "Peru", "name": "Peru"}]
    metrics = (await seeded.get("/v1/analytics/metrics")).json()["metrics"]
    assert [m["key"] for m in metrics] == ["views", "interactions", "comments", "videos"]


async def test_channel_breakdown_counts_pairs_per_topic(seeded):
    body = (await seeded.get("/v1/analytics/channel-breakdown")).json()
    assert body["total"] == {"managed": 6, "active": 5}
    fb, yt = body["by_platform"]
    assert fb == {"platform": "facebook", "managed": 4, "active": 3,
                  "by_group": [{"group": "ITL", "managed": 2, "active": 2}, {"group": "Sible", "managed": 2, "active": 1}],
                  "by_country": [{"country": "Brazil", "managed": 3, "active": 3},
                                 {"country": "Peru", "managed": 1, "active": 0}]}
    assert yt == {"platform": "youtube", "managed": 2, "active": 2,
                  "by_country": [{"country": "Khác", "managed": 1, "active": 1},
                                 {"country": "Peru", "managed": 1, "active": 1}]}


async def test_viewer_only_sees_own_data_and_admin_sees_everyone(seeded):
    own = (await seeded.get("/v1/analytics/summary", sub=OWN_B, **RANGE)).json()
    assert own["total_views"] == 999 and own["videos_posted"] == 1 and own["managed_channels"] == 1
    admin_all = (await seeded.get("/v1/analytics/summary", sub=ADMIN, role="admin", **RANGE)).json()
    assert admin_all["total_views"] == 560 + 999 and admin_all["filter"]["owner_account_id"] is None
    admin_b = (await seeded.get("/v1/analytics/summary", sub=ADMIN, role="dash_admin", owner_account_id=OWN_B,
                                **RANGE)).json()
    assert admin_b["total_views"] == 999 and admin_b["filter"]["owner_account_id"] == OWN_B


async def test_viewer_is_forced_to_self_even_when_asking_for_someone_else(seeded):
    r = (await seeded.get("/v1/analytics/summary", sub=OWN_A, owner_account_id=OWN_B, **RANGE)).json()
    assert r["total_views"] == 560 and r["filter"]["owner_account_id"] == OWN_A
    for path in ("traffic", "top-channels", "views-by-country", "channel-breakdown", "country-tags"):
        body = (await seeded.get("/v1/analytics/" + path, sub=OWN_A, owner_account_id=OWN_B, **RANGE)).json()
        assert "pgB" not in str(body) and body["filter"]["owner_account_id"] == OWN_A


async def test_invalid_range_is_empty_not_an_error(seeded):
    inverted = (await seeded.get("/v1/analytics/summary", date_from="2026-10-03", date_to="2026-10-01")).json()
    assert inverted["total_views"] == 0 and inverted["days_in_range"] == 0 and inverted["videos_posted"] == 0
    too_long = (await seeded.get("/v1/analytics/traffic", date_from="2024-01-01", date_to="2026-10-01")).json()
    assert too_long["days"] == [] and too_long["totals"]["views"] == 0
    bad = await seeded.get("/v1/analytics/summary", date_from="khong-phai-ngay")
    assert bad.status_code == 422


async def test_default_range_is_last_30_days(seeded):
    body = (await seeded.get("/v1/analytics/summary")).json()
    assert body["filter"]["date_to"] == "2026-10-05" and body["filter"]["date_from"] == "2026-09-06"
    assert body["days_in_range"] == 30 and body["total_views"] == 560 + 11


async def test_post_with_null_views_alone_on_a_day_is_null_not_zero(seeded):
    """Ngày chỉ có bài chưa có số liệu (views=null): KHÔNG được thành 0; vẫn tính là một bài (videos)."""
    from analytics_testsupport import post
    seeded.integration.commit(owner=OWN_A, platform="facebook",
                              content=[post("pnull", "pg1", "2026-10-06", None, None, None)])
    await seeded.sync()
    body = (await seeded.get("/v1/analytics/traffic", date_from="2026-10-05", date_to="2026-10-07")).json()
    assert body["days"][1] == {"date": "2026-10-06", "views": None, "interactions": 0, "comments": 0, "videos": 1}
    assert body["days"][0]["views"] is None and body["days"][2]["views"] is None
    assert body["totals"]["views"] == 0 and body["totals"]["videos"] == 1


async def test_previous_period_is_the_adjacent_window_of_equal_length(seeded):
    """Kỳ trước của 10-01..10-03 là 09-28..09-30: bài ở 09-28 tính, bài ở 09-27 thì không."""
    from analytics_testsupport import event, post
    seeded.integration.commit(owner=OWN_A, platform="facebook",
                              content=[post("edge1", "pg1", "2026-09-28", 4), post("edge0", "pg1", "2026-09-27", 100)],
                              events=[event("ev-edge1", "pg1", "2026-09-28"), event("ev-edge0", "pg1", "2026-09-27")])
    await seeded.sync()
    s = (await seeded.get("/v1/analytics/summary", **RANGE)).json()
    assert s["total_views_prev"] == 11 + 4 and s["videos_posted_prev"] == 2
