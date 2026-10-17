"""Trang quản trị "Kênh theo người dùng": gom kênh theo user và cho biết Tool dùng chung có thấy từng kênh không."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import ts

from integration_service.resources.user_channels import build_user_channels, shared_tool_visible

PATH = "/v1/integrations/admin/user-channels"
RB = "/v1/integrations/resources/batch"
T = datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc)


def row(owner="o1", name="alice", rid="r1", *, mode="fallback_tool", removed=False, state="active", rtype="channel", platform="youtube", **kw):
    base = {"id": rid, "owner_account_id": owner, "owner_username": name, "platform": platform, "resource_type": rtype, "external_id": rid,
            "display_name": "", "url": "", "origin": "web_manual", "execution_mode": mode, "state": state, "removed": removed,
            "created_at": T, "updated_at": T, "last_job_status": None, "last_job_at": None, "content_count": 0, "last_observed_at": None}
    return {**base, **kw}


# ── Quy tắc "Tool dùng chung có thấy kênh này không" (đúng như system_api.shared_view) ─────────

def test_only_live_shared_channels_are_visible_to_the_shared_tool():
    assert shared_tool_visible(row(mode="fallback_tool"))
    assert shared_tool_visible(row(mode="fallback_tool", state="disabled"))      # kênh tắt vẫn hiện ở Tool (cờ enabled=false)
    assert not shared_tool_visible(row(mode="primary_tool"))
    assert not shared_tool_visible(row(mode="fallback_tool", removed=True))
    assert not shared_tool_visible(row(mode="fallback_tool", rtype="group"))


def test_user_whose_channels_are_all_removed_is_not_visible_in_the_shared_tool_but_is_still_listed():
    out = build_user_channels([row(rid="a", removed=True), row(rid="b", removed=True), row(owner="o2", name="bob", rid="c")])
    alice, bob = out["users"]
    assert alice["username"] == "alice" and alice["visible_in_shared_tool"] is False
    assert alice["totals"] == {"active": 0, "removed": 2, "shared_tool": 0, "personal_tool": 0}
    assert bob["visible_in_shared_tool"] is True
    assert out["summary"] == {"users": 2, "visible_in_shared_tool": 1, "active_channels": 1, "shared_tool_channels": 1, "removed_channels": 2}


def test_totals_separate_shared_personal_and_removed_channels_and_users_are_sorted_by_name():
    out = build_user_channels([row(owner="o2", name="Zed", rid="z"), row(rid="a1"), row(rid="a2", mode="primary_tool"), row(rid="a3", removed=True)])
    assert [u["username"] for u in out["users"]] == ["alice", "Zed"]
    assert out["users"][0]["totals"] == {"active": 2, "removed": 1, "shared_tool": 1, "personal_tool": 1}


def test_user_without_a_known_username_falls_back_to_the_account_id():
    assert build_user_channels([row(owner="acc_x", name="")])["users"][0]["username"] == "acc_x"


# ── Endpoint ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.needs_pg
async def test_admin_sees_every_users_channels_with_the_shared_tool_flag(env):
    tool = await env.tool()
    items = [{"platform": "facebook", "resource_type": "page", "external_id": e, "observed_at": ts(), "display_name": e} for e in ("p1", "p2", "p3")]
    ids = [i["resource_id"] for i in (await tool.post(RB, {"items": items})).json()["data"]["items"]]
    async with env.db.connection() as conn:
        await conn.execute("UPDATE resources SET execution_mode = 'fallback_tool' WHERE id = $1", ids[0])
        await conn.execute("UPDATE resources SET removed = true WHERE id = $1", ids[2])
    admin, viewer = await env.verified_user(role="admin"), await env.verified_user()
    assert (await env.http.get(PATH, headers=viewer["headers"])).status_code == 403
    assert (await env.http.get(PATH)).status_code in (401, 403)

    data = (await env.http.get(PATH, headers=admin["headers"])).json()["data"]
    assert len(data["users"]) == 1 and data["users"][0]["owner_account_id"] == tool.owner
    by_id = {c["id"]: c for c in data["users"][0]["channels"]}
    assert by_id[ids[0]]["shared_tool_visible"] is True and by_id[ids[0]]["execution_mode"] == "fallback_tool"
    assert by_id[ids[1]]["shared_tool_visible"] is False and by_id[ids[1]]["origin"] == "tool"
    assert by_id[ids[2]]["removed"] is True and by_id[ids[2]]["shared_tool_visible"] is False
    assert data["users"][0]["totals"] == {"active": 2, "removed": 1, "shared_tool": 1, "personal_tool": 1}
    assert data["summary"]["visible_in_shared_tool"] == 1

    live = (await env.http.get(PATH, headers=admin["headers"], params={"include_removed": "false"})).json()["data"]
    assert {c["id"] for c in live["users"][0]["channels"]} == {ids[0], ids[1]}
    none = (await env.http.get(PATH, headers=admin["headers"], params={"owner_account_id": "acc_khong_co"})).json()["data"]
    assert none["users"] == [] and none["summary"]["users"] == 0
