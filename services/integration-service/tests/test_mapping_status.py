"""`mapping_status` = "đã liên kết số liệu": suy ra từ dữ liệu thật (kênh đã có video/chỉ số), không phải cờ lưu sẵn không ai cập nhật."""

from __future__ import annotations

import pytest
from helpers import commit, ingest_full, new_snapshot, post, push, res

pytestmark = pytest.mark.needs_pg
BASE = "/v1/integrations"
RES = "/v1/resources"


async def _shared_items(env) -> dict[str, dict]:
    shared = await env.tool(kind="fallback_tool", platforms=("facebook", "youtube", "tiktok"))
    d = (await shared.get(f"{BASE}/resources")).json()["data"]
    return {i["external_id"]: i for i in d["items"]}


async def test_machine_list_reports_mapped_and_data_counts_only_for_channels_with_data(env):
    owner = await env.verified_user()
    tool = await env.tool(user=owner, kind="primary_tool")
    await ingest_full(tool, resources=[res("has_data"), res("no_data")], content=[post("v1", res_ext="has_data"), post("v2", res_ext="has_data")])
    items = await _shared_items(env)
    full, empty = items["has_data"], items["no_data"]
    assert full["mapping_status"] == "mapped" and full["content_count"] == 2
    assert full["last_collected_at"] and full["last_collected_at"].startswith("2026-")
    assert empty["mapping_status"] == "unmapped" and empty["content_count"] == 0 and empty["last_collected_at"] is None


async def test_removed_content_does_not_count_as_data(env):
    owner = await env.verified_user()
    tool = await env.tool(user=owner, kind="primary_tool")
    await ingest_full(tool, resources=[res("r1")], content=[post("v1", res_ext="r1")])
    await env.rows("UPDATE content_items SET removed = true RETURNING id")
    assert (await _shared_items(env))["r1"]["mapping_status"] == "unmapped"


async def test_website_channel_becomes_mapped_once_data_arrives_and_filter_follows(env):
    """Kênh nhập ở website (legacy) hiện `unmapped`; sau khi có số liệu thì `mapped` ở danh sách lẫn bộ lọc — không cần ai sửa cờ."""
    u = await env.verified_user()
    await env.projection(u["sub"])
    h = u["headers"]
    src = (await env.http.post(f"{RES}/sources", headers=h, json={"name": "YT", "platform": "youtube"})).json()
    web = (await env.http.post(RES, headers=h, json={"source_instance_id": src["id"], "platform": "youtube", "input_ref": "UCmapme"})).json()
    assert web["mapping_status"] == "unmapped"
    listed = lambda **p: env.http.get(RES, headers=h, params=p)   # noqa: E731
    assert [i["id"] for i in (await listed(mapping_status="unmapped")).json()["items"]] == [web["id"]]
    row = (await env.rows("SELECT external_id FROM resources WHERE id = $1", web["id"]))[0]
    tool = await env.tool(user=u, kind="primary_tool", platforms=("youtube",))
    sid = await new_snapshot(tool, platform="youtube", type="delta", expected={"content": 1})
    await push(tool, sid, "content", [post("v1", res_ext=row["external_id"])])
    await commit(tool, sid)
    got = (await listed()).json()["items"][0]
    assert got["id"] == web["id"] and got["mapping_status"] == "mapped"
    assert (await listed(mapping_status="unmapped")).json()["items"] == []
    assert [i["id"] for i in (await listed(mapping_status="mapped")).json()["items"]] == [web["id"]]
