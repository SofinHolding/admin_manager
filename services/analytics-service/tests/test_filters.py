"""Bộ lọc: quốc gia / nền tảng / người / kênh / nhóm / chủ đề, TẤT CẢ cùng lúc, và MỘT FilterContext cho mọi endpoint.

Số kỳ vọng tính tay từ fixture `seed_world` (xem docstring test_stats_exact.py).
"""

import pytest

from analytics_testsupport import ADMIN, OWN_A, OWN_B

pytestmark = pytest.mark.needs_pg

RANGE = {"date_from": "2026-10-01", "date_to": "2026-10-03"}
KEYS = ("total_views", "total_views_prev", "videos_posted", "videos_posted_prev", "active_channels",
        "managed_channels", "days_in_range")


def core(body: dict) -> dict:
    return {k: body[k] for k in KEYS}


async def make_group(w, sub=OWN_A, name="G1", members=()):
    r = await w.client.post("/v1/analytics/groups", json={"name": name}, headers=w.as_user(sub))
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    for ref, tag, override in members:
        m = await w.client.post(f"/v1/analytics/groups/{gid}/members", headers=w.as_user(sub),
                                json={"channel_ref": ref, "platform_tag": tag, "country_override": override})
        assert m.status_code == 200, m.text
    return gid


async def test_country_filter_is_applied_to_kpi_traffic_and_top(seeded):
    s = (await seeded.get("/v1/analytics/summary", country="Peru", **RANGE)).json()
    assert core(s) == {"total_views": 110, "total_views_prev": 0, "videos_posted": 2, "videos_posted_prev": 0,
                       "active_channels": 1, "managed_channels": 2, "days_in_range": 3}
    t = (await seeded.get("/v1/analytics/traffic", country="Peru", **RANGE)).json()
    assert t["days"] == [{"date": "2026-10-01", "views": 40, "interactions": 0, "comments": 0, "videos": 1},
                         {"date": "2026-10-02", "views": 0, "interactions": 0, "comments": 0, "videos": 0},
                         {"date": "2026-10-03", "views": 70, "interactions": 7, "comments": 7, "videos": 1}]
    assert t["country"] == "Peru" and t["scope_label"] == "Tổng — cả hai nền tảng · Peru"
    top = (await seeded.get("/v1/analytics/top-channels", country="peru", **RANGE)).json()["items"]
    assert [(i["id"], i["views"]) for i in top] == [("pg3", 70), ("youtube:ch1", 40)]   # không phân biệt hoa/thường
    cb = (await seeded.get("/v1/analytics/channel-breakdown", country="Peru")).json()
    assert cb["total"] == {"managed": 2, "active": 1}


async def test_country_filter_matches_unknown_bucket(seeded):
    s = (await seeded.get("/v1/analytics/summary", country="Khác", **RANGE)).json()
    assert s["total_views"] == 70 and s["managed_channels"] == 1     # chỉ ch2 (quốc gia trống)


async def test_country_comparison_chart_ignores_country_filter_but_keeps_others(seeded):
    plain = (await seeded.get("/v1/analytics/views-by-country", **RANGE)).json()
    peru = (await seeded.get("/v1/analytics/views-by-country", country="Peru", **RANGE)).json()
    assert peru["countries"] == plain["countries"] and peru["totals"] == plain["totals"]
    assert peru["ignored_filters"] == ["country"]
    only_yt = (await seeded.get("/v1/analytics/views-by-country", platform="youtube", **RANGE)).json()
    assert only_yt["totals"] == {"Peru": 40, "Khác": 70}      # bộ lọc khác vẫn thu hẹp biểu đồ


async def test_platform_filter(seeded):
    yt = (await seeded.get("/v1/analytics/summary", platform="youtube", **RANGE)).json()
    assert core(yt) == {"total_views": 110, "total_views_prev": 0, "videos_posted": 1, "videos_posted_prev": 0,
                        "active_channels": 2, "managed_channels": 2, "days_in_range": 3}
    fb = (await seeded.get("/v1/analytics/summary", platform="facebook", **RANGE)).json()
    assert core(fb) == {"total_views": 450, "total_views_prev": 11, "videos_posted": 4, "videos_posted_prev": 1,
                        "active_channels": 2, "managed_channels": 3, "days_in_range": 3}
    t = (await seeded.get("/v1/analytics/traffic", platform="youtube", **RANGE)).json()
    assert t["scope"] == "youtube" and t["scope_label"] == "YouTube"
    assert [d["views"] for d in t["days"]] == [100, 0, 10] and t["totals"]["interactions"] == 0


async def test_legacy_platform_tag_native_value_means_platform(seeded):
    a = (await seeded.get("/v1/analytics/summary", platform_tag="youtube", **RANGE)).json()
    b = (await seeded.get("/v1/analytics/summary", platform="youtube", **RANGE)).json()
    assert core(a) == core(b) and (await seeded.get("/v1/analytics/traffic", platform_tag="youtube",
                                                    **RANGE)).json()["scope"] == "youtube"


async def test_project_filter_uses_bindings_and_event_project(seeded):
    itl = (await seeded.get("/v1/analytics/summary", project="itl", **RANGE)).json()
    assert core(itl) == {"total_views": 380, "total_views_prev": 11, "videos_posted": 3, "videos_posted_prev": 1,
                         "active_channels": 2, "managed_channels": 2, "days_in_range": 3}
    sib = (await seeded.get("/v1/analytics/summary", project="sib", **RANGE)).json()
    assert core(sib) == {"total_views": 420, "total_views_prev": 11, "videos_posted": 1, "videos_posted_prev": 0,
                         "active_channels": 1, "managed_channels": 2, "days_in_range": 3}


async def test_person_filter_for_admin_only(seeded):
    b = (await seeded.get("/v1/analytics/top-channels", sub=ADMIN, role="admin", owner_account_id=OWN_B,
                          **RANGE)).json()["items"]
    assert [i["id"] for i in b] == ["pgB"] and b[0]["views"] == 999
    # viewer A xin dữ liệu của B: bị ép về A
    a = (await seeded.get("/v1/analytics/top-channels", sub=OWN_A, owner_account_id=OWN_B, **RANGE)).json()["items"]
    assert "pgB" not in [i["id"] for i in a]


async def test_group_filter_platform_tag_and_country_override(seeded):
    gid = await make_group(seeded, members=[("pg1", "tiktok", "Chile"), ("ch1", "", "")])
    s = (await seeded.get("/v1/analytics/summary", group_id=gid, **RANGE)).json()
    assert s["total_views"] == 390 and s["managed_channels"] == 2
    # override quốc gia của nhóm LUÔN áp dụng (không cần lọc theo nhóm): pg1 thành Chile ở biểu đồ quốc gia
    vbc = (await seeded.get("/v1/analytics/views-by-country", **RANGE)).json()
    assert vbc["totals"] == {"Chile": 350, "Brazil": 30, "Peru": 110, "Khác": 70}
    chile = (await seeded.get("/v1/analytics/summary", country="Chile", **RANGE)).json()
    assert chile["total_views"] == 350
    tag = (await seeded.get("/v1/analytics/summary", platform_tag="tiktok", **RANGE)).json()
    assert tag["total_views"] == 350 and tag["managed_channels"] == 1
    assert (await seeded.get("/v1/analytics/platform-tags")).json()["items"] == ["tiktok"]
    cb = (await seeded.get("/v1/analytics/channel-breakdown")).json()
    assert {"platform": "tiktok", "managed": 1, "active": 1, "is_custom_tag": True} in cb["by_platform"]


async def test_group_of_someone_else_is_not_usable(seeded):
    gid_b = await make_group(seeded, sub=OWN_B, name="GB", members=[("pgB", "x", "")])
    # viewer A dùng group_id của B -> không có nhóm trong phạm vi của A -> rỗng (không lộ dữ liệu B)
    s = (await seeded.get("/v1/analytics/summary", sub=OWN_A, group_id=gid_b, **RANGE)).json()
    assert s["total_views"] == 0 and s["managed_channels"] == 0
    # admin chọn đúng owner B thì dùng được
    ok = (await seeded.get("/v1/analytics/summary", sub=ADMIN, role="admin", owner_account_id=OWN_B,
                           group_id=gid_b, **RANGE)).json()
    assert ok["total_views"] == 999


async def test_all_filters_at_once(seeded):
    gid = await make_group(seeded, members=[("pg1", "tiktok", "Chile"), ("ch1", "", "")])
    params = dict(owner_account_id=OWN_A, platform="facebook", country="Chile", project="itl", group_id=gid,
                  platform_tag="tiktok", **RANGE)
    s = (await seeded.get("/v1/analytics/summary", **params)).json()
    assert core(s) == {"total_views": 350, "total_views_prev": 11, "videos_posted": 2, "videos_posted_prev": 1,
                       "active_channels": 1, "managed_channels": 1, "days_in_range": 3}
    top = (await seeded.get("/v1/analytics/top-channels", **params)).json()["items"]
    assert [(i["id"], i["country"], i["views"]) for i in top] == [("pg1", "Chile", 350)]
    assert s["filter"] == {"date_from": "2026-10-01", "date_to": "2026-10-03", "owner_account_id": OWN_A,
                           "platform": "facebook", "country": "Chile", "project": "itl", "group_id": gid,
                           "platform_tag": "tiktok"}
    # một điều kiện mâu thuẫn (project không phải của pg1) -> rỗng, không phải lỗi
    none = (await seeded.get("/v1/analytics/summary", **{**params, "project": "khong-co"})).json()
    assert none["total_views"] == 0 and none["managed_channels"] == 0


DATA_ENDPOINTS = ["summary", "traffic", "top-channels", "views-by-country", "country-tags", "platform-tags",
                  "metrics", "channel-breakdown", "compare/platform", "compare/owner", "compare/country",
                  "compare/channel", "compare/group", "compare/project"]


async def test_one_filter_context_for_every_data_endpoint(seeded):
    """Mọi endpoint dữ liệu nhận CÙNG tham số và trả CÙNG khối `filter` đã áp dụng."""
    gid = await make_group(seeded, members=[("pg1", "tiktok", "")])
    params = dict(date_from="2026-10-01", date_to="2026-10-03", owner_account_id=OWN_A, platform="facebook",
                  country="Brazil", project="itl", group_id=gid, platform_tag="tiktok")
    filters = {}
    for ep in DATA_ENDPOINTS:
        r = await seeded.get("/v1/analytics/" + ep, **params)
        assert r.status_code == 200, (ep, r.text)
        filters[ep] = r.json()["filter"]
        assert "freshness" in r.json(), ep
    assert len({str(sorted(f.items())) for f in filters.values()}) == 1, filters
    assert filters["summary"] == {**params}
    # alias legacy dùng cùng bộ lọc
    for ep in ("summary", "traffic", "top-channels", "views-by-country", "country-tags", "platform-tags", "metrics",
               "channel-breakdown"):
        legacy = await seeded.get("/v1/viewer/" + ep, **params)
        assert legacy.status_code == 200 and legacy.json()["filter"] == filters["summary"], ep


async def test_openapi_declares_the_same_filter_params_on_every_data_endpoint(seeded):
    spec = (await seeded.client.get("/openapi.json")).json()
    expected = {"date_from", "date_to", "owner_account_id", "platform", "country", "project", "group_id", "platform_tag"}
    for ep in DATA_ENDPOINTS:
        path = "/v1/analytics/" + ep.replace("compare/platform", "compare/{dimension}")
        if ep.startswith("compare/") and ep != "compare/platform":
            continue
        names = {p["name"] for p in spec["paths"][path]["get"]["parameters"]}
        assert expected <= names, (ep, expected - names)


async def test_compare_dimensions_are_exact(seeded):
    async def items(dim, **kw):
        r = await seeded.get("/v1/analytics/compare/" + dim, **{**RANGE, **kw})
        assert r.status_code == 200, r.text
        return {i["id"]: i for i in r.json()["items"]}, r.json()

    plat, body = await items("platform")
    assert [(i["id"], i["total"]) for i in body["items"]] == [("facebook", 450), ("youtube", 110)]
    assert [p["views"] for p in plat["facebook"]["series"]] == [150, 230, 70]
    assert [p["views"] for p in plat["youtube"]["series"]] == [100, 0, 10]

    country, cbody = await items("country", country="Peru")       # chiều quốc gia không bị thu hẹp theo country
    assert {k: v["total"] for k, v in country.items()} == {"Brazil": 380, "Peru": 110, "Khác": 70}
    assert cbody["ignored_filters"] == ["country"]

    chan, _ = await items("channel")
    assert {k: v["total"] for k, v in chan.items()} == {"facebook:pg1": 350, "youtube:ch2": 70, "facebook:pg3": 70,
                                                       "youtube:ch1": 40, "facebook:pg2": 30}
    proj, _ = await items("project")
    assert {k: v["total"] for k, v in proj.items()} == {"sib": 420, "itl": 380, "Không chủ đề": 110}
    assert proj["sib"]["name"] == "Sible"

    own, _ = await items("owner", sub=ADMIN, role="admin")
    assert {k: v["total"] for k, v in own.items()} == {OWN_A: 560, OWN_B: 999}
    # người thường chỉ thấy chính mình ở chiều "owner"
    mine, _ = await items("owner", sub=OWN_A)
    assert set(mine) == {OWN_A}

    bad = await seeded.get("/v1/analytics/compare/khong-co", **RANGE)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


async def test_compare_group_dimension_and_none_vs_zero(seeded):
    gid = await make_group(seeded, members=[("pg1", "", ""), ("pg3", "", "")])
    r = (await seeded.get("/v1/analytics/compare/group", **RANGE)).json()
    item = next(i for i in r["items"] if i["id"] == gid)
    assert item["total"] == 420 and [p["views"] for p in item["series"]] == [150, 200, 70]
    # ch1: ngày 10-03 CHƯA có số liệu (null) khác ngày 10-02 bằng 0
    ch = (await seeded.get("/v1/analytics/compare/channel", **RANGE)).json()
    ch1 = next(i for i in ch["items"] if i["id"] == "youtube:ch1")
    assert [p["views"] for p in ch1["series"]] == [40, 0, None]


async def test_country_alias_normalizes_everywhere(seeded):
    admin = seeded.as_user(ADMIN, "admin")
    assert (await seeded.client.post("/v1/analytics/admin/country-canonicals", json={"name": "Brazil"},
                                     headers=admin)).status_code == 200
    assert (await seeded.client.post("/v1/analytics/admin/country-aliases", json={"alias": "BR", "canonical": "brazil"},
                                     headers=admin)).json() == {"alias": "br", "canonical": "Brazil"}
    # đổi quốc gia gốc của pg2 thành "br" trên nguồn -> sau đồng bộ vẫn gộp về Brazil
    seeded.integration.update_item("resources", owner_account_id=OWN_A, platform="facebook", external_id="pg2",
                                   country={"name": "BR"})
    await seeded.sync()
    vbc = (await seeded.get("/v1/analytics/views-by-country", **RANGE)).json()
    assert vbc["totals"]["Brazil"] == 380 and "BR" not in vbc["countries"]
    br = (await seeded.get("/v1/analytics/summary", country="br", **RANGE)).json()
    assert br["total_views"] == 380 and br["filter"]["country"] == "Brazil"


async def test_other_owners_group_settings_never_leak_into_my_numbers(seeded):
    """Override quốc gia / nhãn của nhóm thuộc người khác (trùng channel_ref) không được ảnh hưởng số của tôi."""
    await make_group(seeded, sub=OWN_B, name="GB", members=[("pg1", "leak-tag", "Atlantis")])
    vbc = (await seeded.get("/v1/analytics/views-by-country", sub=OWN_A, **RANGE)).json()
    assert "Atlantis" not in vbc["countries"] and vbc["totals"] == {"Brazil": 380, "Peru": 110, "Khác": 70}
    assert (await seeded.get("/v1/analytics/platform-tags", sub=OWN_A)).json()["items"] == []
    assert (await seeded.get("/v1/analytics/summary", sub=OWN_A, platform_tag="leak-tag", **RANGE)).json()[
        "total_views"] == 0
    groups = (await seeded.get("/v1/analytics/compare/group", sub=OWN_A, **RANGE)).json()["items"]
    assert groups == []
