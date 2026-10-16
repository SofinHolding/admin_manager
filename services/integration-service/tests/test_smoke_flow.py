"""Luồng chính end-to-end trên Postgres thật: user tạo client + key -> handshake -> snapshot -> commit -> canonical."""

from __future__ import annotations

import pytest
from helpers import canonical, commit, daily, event, ingest_full, new_snapshot, post, push, res

pytestmark = pytest.mark.needs_pg


async def test_full_happy_path(env):
    tool = await env.tool()
    hs = await tool.post("/v1/integrations/handshake", {
        "installation": {"device_id": tool.device_id, "hostname": "PC", "os": "windows", "app_version": "1"},
        "platforms": ["facebook"], "capabilities": ["snapshots.push", "resources.push", "unknown.cap"]})
    assert hs.status_code == 200, hs.text
    data = hs.json()["data"]
    assert data["owner_account_id"] == tool.owner and data["client_id"] == tool.client_id
    assert data["accepted_capabilities"] == ["resources.push", "snapshots.push"]

    out = await ingest_full(
        tool, resources=[res("r1", display_name="Page 1")],
        content=[post("p1", views=120, reactions=5, comments=None), post("p2", views=0)],
        metrics=[daily("r1", "2026-10-04", views=77)], events=[event("e1")])
    assert out["status"] == "committed" and out["totals"] == {"resources": 1, "content": 2, "metrics": 1, "events": 1}

    res_items = (await canonical(env, "resources"))["items"]
    assert res_items[0]["external_id"] == "r1" and "project_keys" not in res_items[0]
    content = (await canonical(env, "content"))["items"]
    by_id = {i["external_id"]: i for i in content}
    assert by_id["p1"]["metrics"] == {"views": 120, "reactions": 5, "comments": None}
    assert by_id["p2"]["metrics"] == {"views": 0}
    assert (await canonical(env, "metrics"))["items"][0]["metrics"] == {"views": 77}
    assert (await canonical(env, "events"))["items"][0]["kind"] == "post_ok"
