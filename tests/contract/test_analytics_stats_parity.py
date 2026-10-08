"""Parity analytics-service <-> admin-server legacy: CÙNG một bộ dữ liệu mẫu, so từng phản hồi.

(a) legacy: dữ liệu dạng 7 store (fb_posts, yt_insights_daily, repost_events, repost_pairings, repost_groups,
    fb_page_meta, yt_channels) đọc bởi `LocalFileDb` của admin-server (chạy đúng `stats_core.py` + `db.py` cũ);
(b) analytics: cùng dữ liệu đã dịch sang canonical (đúng bảng ánh xạ ở ingest-data-contract-v1 mục 7, vai trò của Legacy ACL),
    qua fake Integration -> consumer -> Postgres THẬT -> HTTP.

Test chéo hệ thống nên được phép import code legacy (service thì không bao giờ).

KHÁC BIỆT CÓ CHỦ ĐÍCH (đều có assertion riêng bên dưới, không bị che):
  D1. `managed_channels`/`active_channels` của summary: legacy chỉ đếm kênh Facebook có `repost_pairings`; analytics đếm MỌI
      kênh quản lý (kể cả YouTube) để ô KPI khớp tổng của `channel-breakdown` (nơi legacy vốn đã tính cả YouTube).
      => analytics = legacy + số kênh YouTube quản lý/đang chạy.
  D2. Bộ lọc `country` áp cho CẢ KPI phụ (videos_posted, managed, active), vì KPI và chart dùng chung một FilterContext;
      legacy chỉ lọc lượt xem. Parity có lọc quốc gia vì vậy chỉ so các phần lượt xem/traffic/top.
  D3. `views-by-country` của analytics có thêm `ignored_filters` (additive), `filter`, `freshness` ở mọi response (additive).
  D4. `top-channels` đồng hạng: thứ tự do analytics quyết định (ORDER BY ổn định); dữ liệu mẫu tránh đồng hạng.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "services" / "analytics-service"
for p in (ROOT / "legacy" / "admin-server", SERVICE / "src", SERVICE / "tests", ROOT / "packages" / "observability" / "src",
          ROOT / "packages" / "contracts" / "python", ROOT / "packages" / "sdk-python", ROOT / "infra" / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import devenv  # noqa: E402

pytestmark = pytest.mark.skipif(not devenv.pg_ready(), reason=devenv.SKIP_REASON)

import secrets  # noqa: E402

import stats_core as legacy_stats  # noqa: E402,F401  (xác nhận import được đúng file legacy)
from analytics_harness import FakeClock, build_world, dsn, reset_database  # noqa: E402
from analytics_testsupport import (OWN_A, FakeIdentityFeed, FakeIntegration, Issuer, daily, event, post, res,  # noqa: E402
                                   project)
from db import LocalFileDb  # noqa: E402  (legacy legacy/admin-server/db.py)

from analytics_service import migrate  # noqa: E402
from analytics_service.db import Database  # noqa: E402

FROM, TO = "2026-10-01", "2026-10-08"
PARAMS = {"date_from": FROM, "date_to": TO}

# ── Dữ liệu mẫu ở dạng LEGACY (7 store) ─────────────────────────────────────────

FB_POSTS = {
    "page:1": [dict(post_id="p1", date="2026-10-01", views=100, reactions=10, comments_count=1),
               dict(post_id="p2", date="2026-10-01", views=50, reactions=5, comments_count=2),
               dict(post_id="p3", date="2026-10-02", views=200, reactions=20, comments_count=4),
               dict(post_id="p7", date="2026-09-30", views=11, reactions=1, comments_count=1)],
    "page:2": [dict(post_id="p4", date="2026-10-02", views=30, reactions=3, comments_count=0),
               dict(post_id="p6", date="2026-10-03", views=None, reactions=None, comments_count=None)],
    "page:3": [dict(post_id="p5", date="2026-10-03", views=70, reactions=7, comments_count=7),
               dict(post_id="p8", date="2026-10-04", views=0, reactions=0, comments_count=0)],
    "page:4": [dict(post_id="p9", date="2026-10-05", views=15, reactions=1, comments_count=1)],     # không có meta
    "page:5": [dict(post_id="p10", date="2026-10-02", views=5, reactions=0, comments_count=0)],
    "page:6": [dict(post_id="p11", date="2026-10-06", views=9, reactions=0, comments_count=0)],
    "page:7": [dict(post_id="p12", date="2026-10-07", views=13, reactions=0, comments_count=0)],
    "page:8": [dict(post_id="p13", date="2026-10-03", views=21, reactions=2, comments_count=2)],
}
YT_DAILY = {
    "2026-09-29|UC1": dict(channel_id="UC1", date="2026-09-29", views=7),
    "2026-10-01|UC1": dict(channel_id="UC1", date="2026-10-01", views=40),
    "2026-10-02|UC1": dict(channel_id="UC1", date="2026-10-02", views=0),
    "2026-10-04|UC1": dict(channel_id="UC1", date="2026-10-04", views=25),
    "2026-10-01|UC2": dict(channel_id="UC2", date="2026-10-01", views=61),
    "2026-10-03|UC2": dict(channel_id="UC2", date="2026-10-03", views=10),
    "2026-10-05|UC3": dict(channel_id="UC3", date="2026-10-05", views=33),                        # không có meta
}
EVENTS = [
    dict(id="e1", kind="post_ok", date="2026-10-01", target="page:1"),
    dict(id="e2", kind="post_ok", date="2026-10-02", target="page:1"),
    dict(id="e3", kind="post_ok", date="2026-10-02", target="page:2"),
    dict(id="e4", kind="post_ok", date="2026-10-03", target="page:3"),
    dict(id="e5", kind="post_fail", date="2026-10-01", target="page:1"),
    dict(id="e6", kind="post_ok", date="2026-09-30", target="page:1"),                            # kỳ trước
    dict(id="e7", kind="post_ok", date="2026-10-05", target="page:4"),
    dict(id="e8", kind="post_ok", date="2026-10-08", target="page:9"),                            # ngày không có bài -> fallback
]
GROUPS = [dict(id="g-itl", name="ITL", enabled=True, country="Brazil"),
          dict(id="g-sib", name="Sible", enabled=True, country=""),
          dict(id="g-off", name="Off", enabled=False, country="Peru")]
PAIRINGS = {
    "pr1": dict(target="page:1", enabled=True, group_id="g-itl"), "pr2": dict(target="page:1", enabled=True, group_id="g-sib"),
    "pr3": dict(target="page:2", enabled=True, group_id="g-itl"), "pr4": dict(target="page:3", enabled=False, group_id="g-sib"),
    "pr5": dict(target="page:5", enabled=True, group_id="g-off"), "pr6": dict(target="page:6", enabled=True, group_id=""),
    "pr7": dict(target="page:7", enabled=True, group_id="g-gone"), "pr8": dict(target="page:8", enabled=True, group_id="g-itl"),
}
FB_META = {
    "page:1": dict(target="page:1", name="Page One", country="Brazil"),
    "page:2": dict(target="page:2", name="Page Two", country=""),
    "page:3": dict(target="page:3", name="Page Three", country="Peru"),
    "page:5": dict(target="page:5", name="Page Five", country=""),
    "page:6": dict(target="page:6", name="Page Six", country="Chile"),
    "page:7": dict(target="page:7", name="Page Seven", country=""),
    "page:8": dict(target="page:8", name="Page Eight", country="br"),
}
YT_CHANNELS = {"channels": [dict(id="UC1", title="Chan One", country="Peru"), dict(id="UC2", title="Chan Two", country="")]}
N_YT = len(YT_CHANNELS["channels"])


def write_legacy_files(directory: Path) -> None:
    files = {"fb_posts.json": FB_POSTS, "yt_insights_daily.json": YT_DAILY, "repost_events.json": EVENTS,
             "repost_pairings.json": PAIRINGS, "repost_groups.json": GROUPS, "fb_page_meta.json": FB_META,
             "yt_channels.json": YT_CHANNELS}
    for name, data in files.items():
        (directory / name).write_text(json.dumps(data), encoding="utf-8")


def to_canonical(fake: FakeIntegration) -> None:
    """Legacy ACL (ingest-data-contract-v1 mục 7): 7 store cũ -> canonical, rồi COMMIT như một snapshot."""
    bindings: dict[str, list[tuple[str, bool]]] = {}
    for pr in PAIRINGS.values():
        if pr["group_id"]:
            bindings.setdefault(pr["target"], []).append((pr["group_id"], pr["enabled"]))
    resources = []
    for target, meta in FB_META.items():
        no_group = [p for p in PAIRINGS.values() if p["target"] == target and not p["group_id"]]
        all_bindings = [p for p in PAIRINGS.values() if p["target"] == target]
        # managed=True cho page có BẤT KỲ binding nào (grouped hoặc không), khớp với channel_units() logic.
        # active chỉ xét các binding không-group (vì group off/on do group quyết định).
        extra: dict = {"managed": len(all_bindings) > 0}
        if no_group:
            extra["active"] = all(p["enabled"] for p in no_group)
        resources.append(res(target, meta["name"], platform="facebook", country=meta["country"],
                             projects=bindings.get(target), **extra))
    fake.commit(
        owner=OWN_A, platform="facebook",
        projects=[project(g["id"], g["name"], g["country"], g["enabled"]) for g in GROUPS],
        resources=resources,
        content=[post(p["post_id"], target, p["date"], p["views"], p["reactions"], p["comments_count"])
                 for target, posts in FB_POSTS.items() for p in posts],
        events=[event(e["id"], e["target"], e["date"], e["kind"]) for e in EVENTS])
    fake.commit(
        owner=OWN_A, platform="youtube",
        resources=[res(c["id"], c["title"], platform="youtube", country=c["country"], managed=True, active=True,
                       resource_type="channel") for c in YT_CHANNELS["channels"]],
        metrics=[daily(r["channel_id"], r["date"], r["views"]) for r in YT_DAILY.values()])


@pytest_asyncio.fixture
async def legacy(tmp_path):
    write_legacy_files(tmp_path)
    ldb = LocalFileDb(str(tmp_path / "auth.db"), str(tmp_path))
    await ldb.setup()
    ldb._conn.execute("DELETE FROM country_aliases")     # trung lập: không dùng alias mặc định của legacy
    ldb._conn.execute("DELETE FROM country_canonicals")
    ldb._conn.commit()
    return ldb


@pytest_asyncio.fixture
async def analytics():
    await migrate.run(dsn())
    database = Database(dsn(), max_size=5)
    await database.open()
    await reset_database(database)
    keys = {"integration": "feed-" + secrets.token_urlsafe(24), "identity": "feed-" + secrets.token_urlsafe(24)}
    integration, identity, issuer = FakeIntegration(keys["integration"]), FakeIdentityFeed(keys["identity"]), Issuer()
    to_canonical(integration)
    world = await build_world(database, keys, issuer, integration, identity, FakeClock())
    await world.sync()
    yield world
    await world.client.aclose()
    await database.close()


async def get(world, path, **params):
    r = await world.get("/v1/analytics/" + path, **{**PARAMS, **params})
    assert r.status_code == 200, r.text
    return r.json()


def drop_additive(body: dict) -> dict:
    return {k: v for k, v in body.items() if k not in ("filter", "freshness", "linked", "last_synced_at",
                                                       "ignored_filters")}


# ── So sánh ─────────────────────────────────────────────────────────────────────

async def test_summary_parity_with_documented_difference_for_managed_channels(legacy, analytics):
    old = await legacy.summary(FROM, TO)
    new = drop_additive(await get(analytics, "summary"))
    for key in ("total_views", "total_views_prev", "videos_posted", "videos_posted_prev", "days_in_range"):
        assert new[key] == old[key], key
    assert old["total_views"] > 0 and old["videos_posted"] == 6 and old["total_views_prev"] == 18   # fixture có ý nghĩa
    # D1: analytics đếm cả kênh YouTube quản lý
    assert (old["managed_channels"], old["active_channels"]) == (7, 5)
    assert new["managed_channels"] == old["managed_channels"] + N_YT
    assert new["active_channels"] == old["active_channels"] + N_YT


@pytest.mark.parametrize("scope", ["", "youtube", "facebook"])
async def test_traffic_parity(legacy, analytics, scope):
    old = await legacy.traffic_metric(FROM, TO, scope)
    new = drop_additive(await get(analytics, "traffic", platform=scope))
    assert new == old
    assert any(d["views"] is None for d in old["days"]) or scope == "youtube"      # có ngày null (chưa có dữ liệu)


async def test_traffic_parity_with_country_filter(legacy, analytics):
    for country in ("Brazil", "Peru", "Chile"):
        assert drop_additive(await get(analytics, "traffic", country=country)) == await legacy.traffic_metric(
            FROM, TO, "", country)


async def test_top_channels_parity(legacy, analytics):
    old = {"items": await legacy.top_channels(FROM, TO)}
    assert drop_additive(await get(analytics, "top-channels")) == old
    assert len(old["items"]) == 11
    for country in ("Brazil", "Peru"):
        assert drop_additive(await get(analytics, "top-channels", country=country)) == {
            "items": await legacy.top_channels(FROM, TO, country)}
    assert drop_additive(await get(analytics, "top-channels", limit=3)) == {
        "items": await legacy.top_channels(FROM, TO, "", 3)}


async def test_views_by_country_parity(legacy, analytics):
    assert drop_additive(await get(analytics, "views-by-country")) == await legacy.views_by_country(FROM, TO)


async def test_country_tags_parity(legacy, analytics):
    old = await legacy.country_tags()
    new = [i["name"] for i in (await get(analytics, "country-tags"))["items"]]
    assert new == old and "Brazil" in old


async def test_channel_breakdown_parity(legacy, analytics):
    old = await legacy.channel_breakdown()
    new = drop_additive(await get(analytics, "channel-breakdown"))
    assert new == old
    fb = next(p for p in old["by_platform"] if p["platform"] == "facebook")
    assert {"group": "Không chủ đề", "managed": 2, "active": 2} in fb["by_group"]       # page:6 + page:7 (nhóm không tồn tại)


async def test_parity_still_holds_after_country_alias_normalization(legacy, analytics):
    """page:8 gõ tay quốc gia "br": chưa có alias thì là một nước riêng; có alias thì cả hai phía cùng gộp về Brazil."""
    before_old = await legacy.views_by_country(FROM, TO)
    before_new = drop_additive(await get(analytics, "views-by-country"))
    assert before_new == before_old and "br" in before_old["countries"]

    await legacy.create_country_canonical("Brazil")
    await legacy.upsert_country_alias("BR", "Brazil")
    admin = analytics.as_user("d" * 32, "admin")
    assert (await analytics.client.post("/v1/analytics/admin/country-canonicals", json={"name": "Brazil"},
                                        headers=admin)).status_code == 200
    assert (await analytics.client.post("/v1/analytics/admin/country-aliases", json={"alias": "BR", "canonical": "Brazil"},
                                        headers=admin)).status_code == 200
    after_old = await legacy.views_by_country(FROM, TO)
    after_new = drop_additive(await get(analytics, "views-by-country"))
    assert after_new == after_old and "br" not in after_old["countries"]
    assert drop_additive(await get(analytics, "traffic", country="Brazil")) == await legacy.traffic_metric(
        FROM, TO, "", "Brazil")
    assert drop_additive(await get(analytics, "channel-breakdown")) == await legacy.channel_breakdown()
