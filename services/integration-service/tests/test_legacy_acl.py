"""Legacy ACL: từng operation legacy (status + shape + `{detail}`), dữ liệu đi vào CÙNG kho canonical, Run Queue ↔ jobs."""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from conftest import ts
from helpers import canonical, claim, handshake
from legacy_cover import covers

pytestmark = pytest.mark.needs_pg
RES, OPS, LNK = "/v1/resources", "/v1/ops", "/v1/viewer/links"
RESOURCE_KEYS = {"id", "owner_account_id", "platform", "resource_type", "canonical_external_id", "input_ref", "username",
                 "url", "display_name", "country", "origin", "mapping_status", "execution_mode", "verification_status",
                 "enabled", "created_at", "updated_at"}
SOURCE_KEYS = {"id", "owner_account_id", "name", "platform", "project_key", "default_country", "origin", "created_at",
               "updated_at"}
# `display_name` là trường CỘNG THÊM so với hợp đồng legacy (lấy từ Platform Registry); các trường legacy giữ nguyên.
POLICY_KEYS = {"platform", "display_name", "web_entry_enabled", "fallback_enabled", "adapter_status", "updated_by",
               "updated_at"}
ITEM_KEYS = {"id", "batch_id", "resource_id", "source_assignment_id", "owner_account_id", "platform", "status", "priority",
             "attempts", "last_error", "created_at", "updated_at"}


class L:
    """Client legacy: token USER (RS256 giả) + gọi thẳng đường dẫn cũ."""

    def __init__(self, env):
        self.env = env

    def h(self, u: dict) -> dict:
        return u["headers"]

    async def user(self, role: str = "viewer", username: str = "u") -> dict:
        u = self.env.user(role=role, username=username)
        await self.env.projection(u["sub"])
        return u

    async def call(self, method: str, path: str, u: dict | None = None, *, expect: int | None = 200, **kw):
        r = await self.env.http.request(method, path, headers=self.h(u) if u else None, **kw)
        if expect is not None:
            assert r.status_code == expect, f"{method} {path}: {r.status_code} {r.text}"
        return r

    async def source(self, u: dict, platform: str = "facebook", name: str = "Facebook / ITL", **kw) -> dict:
        return (await self.call("POST", f"{RES}/sources", u, json={"name": name, "platform": platform, **kw})).json()

    async def resource(self, u: dict, source: dict, ref: str = "https://fb.com/page1", platform: str = "facebook", **kw) -> dict:
        body = {"source_instance_id": source["id"], "platform": platform, "input_ref": ref, **kw}
        return (await self.call("POST", RES, u, json=body)).json()

    async def canon(self, rid: str) -> dict:
        return (await self.env.rows("SELECT * FROM resources WHERE id = $1", rid))[0]


@pytest.fixture
def lg(env) -> L:
    return L(env)


# ── /v1/resources/platforms & sources ────────────────────────────────────────────────────

@covers("GET /v1/resources/platforms")
async def test_platforms_list_only_web_entry_enabled(lg):
    u = await lg.user()
    body = (await lg.call("GET", f"{RES}/platforms", u)).json()
    assert body == {"items": [{"platform": p, "display_name": n} for p, n in (
        ("facebook", "Facebook"), ("instagram", "Instagram"), ("tiktok", "TikTok"), ("youtube", "YouTube"))]}
    admin = await lg.user("admin")
    await lg.call("PATCH", f"{OPS}/platform-policies/tiktok", admin, json={"web_entry_enabled": False})
    assert {i["platform"] for i in (await lg.call("GET", f"{RES}/platforms", u)).json()["items"]} == {
        "facebook", "instagram", "youtube"}


@covers("POST /v1/resources/sources", "GET /v1/resources/sources")
async def test_source_create_and_list_are_owner_scoped(lg):
    a, b = await lg.user(), await lg.user()
    src = await lg.source(a, project_key=" ITL ", default_country="Brazil", name=" Facebook / ITL ")
    assert set(src) == SOURCE_KEYS and src["owner_account_id"] == a["sub"] and src["origin"] == "web_manual"
    assert src["name"] == "Facebook / ITL" and src["project_key"] == "ITL" and src["default_country"] == "Brazil"
    assert src["created_at"].endswith("+00:00") and src["created_at"] == src["updated_at"]
    await lg.source(a, platform="youtube", name="YouTube / X")
    await lg.source(b, name="Của người khác")
    items = (await lg.call("GET", f"{RES}/sources", a)).json()["items"]
    assert [i["platform"] for i in items] == ["facebook", "youtube"] and all(set(i) == SOURCE_KEYS for i in items)
    assert all(i["owner_account_id"] == a["sub"] for i in items)


async def test_source_create_rejects_disabled_platform_and_bad_body_with_detail(lg):
    u = await lg.user()
    r = await lg.call("POST", f"{RES}/sources", u, expect=400, json={"name": "x", "platform": "myspace"})
    assert r.json() == {"detail": "Nền tảng 'myspace' chưa được phép nhập từ website"}
    r = await lg.call("POST", f"{RES}/sources", u, expect=422, json={"platform": "facebook"})
    detail = r.json()["detail"]
    assert detail[0]["loc"] == ["body", "name"] and detail[0]["type"] == "missing" and "input" not in detail[0]
    r = await lg.call("POST", f"{RES}/sources", u, expect=422, json={"name": "", "platform": "facebook"})
    assert r.json()["detail"][0]["loc"] == ["body", "name"]


@covers("PATCH /v1/resources/sources/{source_id}")
async def test_source_update(lg):
    a, b = await lg.user(), await lg.user()
    src = await lg.source(a)
    assert (await lg.call("PATCH", f"{RES}/sources/{src['id']}", a, json={"name": "Mới", "default_country": "Peru"})).json() == {"ok": True}
    got = (await lg.call("GET", f"{RES}/sources", a)).json()["items"][0]
    assert got["name"] == "Mới" and got["default_country"] == "Peru" and got["project_key"] == ""
    r = await lg.call("PATCH", f"{RES}/sources/{src['id']}", a, expect=400, json={})
    assert r.json() == {"detail": "Không có gì để cập nhật"}
    r = await lg.call("PATCH", f"{RES}/sources/{src['id']}", b, expect=403, json={"name": "Chiếm quyền"})
    assert r.json() == {"detail": "Không có quyền thao tác nguồn này"}
    r = await lg.call("PATCH", f"{RES}/sources/src_khong_co", a, expect=404, json={"name": "x"})
    assert r.json() == {"detail": "Không tìm thấy nguồn (Source)"}


# ── /v1/resources ─────────────────────────────────────────────────────────────────────

@covers("POST /v1/resources", "GET /v1/resources")
async def test_create_resource_goes_through_the_canonical_pipeline(lg, env):
    u = await lg.user()
    src = await lg.source(u, project_key="ITL")
    res = await lg.resource(u, src, "https://fb.com/page1", username=" page1 ", country="Brazil", resource_type="page")
    assert set(res) == RESOURCE_KEYS
    assert res["owner_account_id"] == u["sub"] and res["origin"] == "web_manual" and res["enabled"] is True
    assert res["mapping_status"] == "unmapped" and res["verification_status"] == "unverified"
    assert res["execution_mode"] == "fallback_local"   # facebook: fallback_enabled
    assert res["display_name"] == "https://fb.com/page1" and res["username"] == "page1" and res["country"] == "Brazil"
    assert res["resource_type"] == "page" and res["input_ref"] == "https://fb.com/page1" and res["canonical_external_id"] == ""
    row = await lg.canon(res["id"])   # CÙNG kho canonical với Tool
    assert row["client_id"] == "legacy_web" and row["origin"] == "web_manual" and row["execution_mode"] == "fallback_tool"
    assert row["platform"] == "facebook" and row["revision"] > 0 and row["removed"] is False
    assert row["data"]["username"] == "page1" and row["data"]["country"]["name"] == "Brazil"
    events = await env.rows("SELECT type, data FROM outbox WHERE type = 'resource.changed'")
    assert [e["data"]["resource_id"] for e in events] == [res["id"]]
    assert await env.scalar("SELECT count(*) FROM projects") == 0       # project đã gỡ: nguồn có project_key vẫn không sinh liên kết
    listed = (await lg.call("GET", RES, u)).json()["items"]
    assert [r["id"] for r in listed] == [res["id"]] and set(listed[0]) == RESOURCE_KEYS | {"source_ids"}
    assert listed[0]["source_ids"] == [src["id"]]


@covers("POST /v1/resources")
async def test_capability_manual_channel_mgmt_auto_creates_a_collect_job(lg, env):
    u = lg.env.user(perms=["capability:manual_channel_mgmt"])
    await lg.env.projection(u["sub"])
    src = await lg.source(u)
    res = await lg.resource(u, src, "https://fb.com/auto")
    job = (await env.rows("SELECT * FROM jobs"))[0]
    assert job["resource_id"] == res["id"] and job["kind"] == "collect" and job["owner_account_id"] == u["sub"]
    assert job["required_capability"] == "data.collect" and job["priority"] == 50 and job["status"] == "pending"
    assert job["dedupe_key"] == f"collect:{res['id']}"
    # thêm lại đúng kênh đó (qua nguồn khác): resource identity không đổi => không tạo job thứ hai
    src2 = await lg.source(u, name="Nguồn 2")
    await lg.resource(u, src2, "https://fb.com/auto")
    assert await env.scalar("SELECT count(*) FROM jobs") == 1


async def test_without_the_capability_no_collect_job_is_created(lg, env):
    u = await lg.user()   # không có capability:manual_channel_mgmt
    src = await lg.source(u)
    await lg.resource(u, src, "https://fb.com/manual")
    assert await env.scalar("SELECT count(*) FROM jobs") == 0


async def test_policy_without_fallback_makes_execution_mode_disabled(lg):
    u = await lg.user()
    src = await lg.source(u, platform="instagram")
    res = await lg.resource(u, src, "insta_user", platform="instagram")
    assert res["execution_mode"] == "disabled"


async def test_create_resource_errors_follow_legacy_order_and_messages(lg):
    a, b = await lg.user(), await lg.user()
    src = await lg.source(a)
    body = {"source_instance_id": src["id"], "platform": "facebook", "input_ref": "x"}
    r = await lg.call("POST", RES, b, expect=403, json=body)
    assert r.json() == {"detail": "Không có quyền thao tác nguồn này"}
    r = await lg.call("POST", RES, a, expect=404, json={**body, "source_instance_id": "src_none"})
    assert r.json() == {"detail": "Không tìm thấy nguồn (Source)"}
    r = await lg.call("POST", RES, a, expect=400, json={**body, "platform": "myspace"})
    assert "chưa được phép nhập từ website" in r.json()["detail"]
    r = await lg.call("POST", RES, a, expect=422, json={**body, "input_ref": ""})
    assert r.json()["detail"][0]["loc"] == ["body", "input_ref"]
    assert await lg.env.scalar("SELECT count(*) FROM resources") == 0


async def test_recreating_same_input_returns_the_existing_canonical_identity(lg, env):
    u = await lg.user()
    s1, s2 = await lg.source(u), await lg.source(u, name="Nguồn 2")
    first = await lg.resource(u, s1, "dup")
    second = await lg.resource(u, s2, "dup")
    assert second["id"] == first["id"] and await env.scalar("SELECT count(*) FROM resources") == 1
    listed = (await lg.call("GET", RES, u)).json()["items"]
    assert sorted(listed[0]["source_ids"]) == sorted([s1["id"], s2["id"]])


async def test_list_filters(lg):
    u, other = await lg.user(), await lg.user()
    s1, s2 = await lg.source(u), await lg.source(u, platform="youtube", name="YT")
    a = await lg.resource(u, s1, "alpha page", display_name="Alpha", country="Brazil")
    await lg.resource(u, s2, "beta chan", platform="youtube", display_name="Beta", country="Peru")
    await lg.resource(u, s1, "gamma", platform="facebook", display_name="Gamma")
    await lg.resource(other, await lg.source(other), "other-one")
    async def names(**params):
        r = await lg.call("GET", RES, u, params=params)
        return sorted(x["display_name"] for x in r.json()["items"])

    assert await names() == ["Alpha", "Beta", "Gamma"]
    assert await names(platform="youtube") == ["Beta"]
    assert await names(country="Brazil") == ["Alpha"]
    assert await names(source_instance_id=s2["id"]) == ["Beta"]
    assert await names(q="alp") == ["Alpha"] and await names(q="CHAN") == ["Beta"]
    assert await names(execution_mode="fallback_local") == ["Alpha", "Beta", "Gamma"]
    assert await names(execution_mode="disabled") == []
    assert await names(origin="web_manual") == ["Alpha", "Beta", "Gamma"] and await names(origin="tool") == []
    assert await names(enabled="true") == ["Alpha", "Beta", "Gamma"] and await names(enabled="false") == []
    assert await names(mapping_status="mapped") == [] and await names(verification_status="unverified") == [
        "Alpha", "Beta", "Gamma"]
    assert a["id"]


@covers("POST /v1/resources/import")
async def test_import_counts_skips_and_dedupes(lg, env):
    u = await lg.user()
    src = await lg.source(u)
    await lg.resource(u, src, "https://fb.com/old", resource_type="page")
    body = {"source_instance_id": src["id"], "platform": "facebook", "resource_type": "page",
            "lines": ["https://fb.com/a", "  https://fb.com/b  ", "", "   ", "https://fb.com/a", "https://fb.com/old"]}
    out = (await lg.call("POST", f"{RES}/import", u, json=body)).json()
    assert set(out) == {"created", "skipped", "created_count", "skipped_count"}
    assert out["created_count"] == 2 and out["skipped_count"] == 2
    assert [c["input_ref"] for c in out["created"]] == ["https://fb.com/a", "https://fb.com/b"]
    assert all(set(c) == RESOURCE_KEYS and c["display_name"] == c["input_ref"] for c in out["created"])
    assert {(s["input_ref"], s["reason"]) for s in out["skipped"]} == {
        ("https://fb.com/a", "duplicate_in_request"), ("https://fb.com/old", "already_exists")}
    assert await env.scalar("SELECT count(*) FROM resources") == 3
    r = await lg.call("POST", f"{RES}/import", u, expect=422, json={**body, "lines": ["x"] * 501})
    assert r.json()["detail"][0]["loc"] == ["body", "lines"]
    r = await lg.call("POST", f"{RES}/import", u, expect=422, json={**body, "lines": []})
    assert r.status_code == 422


@covers("PATCH /v1/resources/{resource_id}")
async def test_update_resource_changes_display_only(lg, env):
    u, other = await lg.user(), await lg.user()
    src = await lg.source(u)
    res = await lg.resource(u, src, "ref1", display_name="Cũ", country="Brazil")
    before = (await lg.canon(res["id"]))["revision"]
    r = await lg.call("PATCH", f"{RES}/{res['id']}", u, json={"display_name": "Mới", "username": "newuser", "country": "",
                                                                "execution_mode": "primary_tool", "enabled": False})
    assert r.json() == {"ok": True}
    got = (await lg.call("GET", RES, u)).json()["items"][0]
    assert got["display_name"] == "Mới" and got["username"] == "newuser" and got["country"] == ""
    assert got["execution_mode"] == "fallback_local" and got["enabled"] is True   # trường vận hành bị bỏ qua
    assert (await lg.canon(res["id"]))["revision"] > before   # mỗi thay đổi có revision mới
    r = await lg.call("PATCH", f"{RES}/{res['id']}", u, expect=400, json={})
    assert r.json() == {"detail": "Không có gì để cập nhật"}
    r = await lg.call("PATCH", f"{RES}/{res['id']}", other, expect=403, json={"display_name": "chiếm"})
    assert r.json() == {"detail": "Không có quyền thao tác resource này"}
    r = await lg.call("PATCH", f"{RES}/res_none", u, expect=404, json={"display_name": "x"})
    assert r.json() == {"detail": "Không tìm thấy resource"}
    assert (await lg.call("GET", RES, u)).json()["items"][0]["display_name"] == "Mới"


@covers("DELETE /v1/resources/{resource_id}")
async def test_delete_resource_removes_from_canonical_history_kept(lg, env):
    u, other = await lg.user(), await lg.user()
    src = await lg.source(u)
    res = await lg.resource(u, src, "bye")
    r = await lg.call("DELETE", f"{RES}/{res['id']}", other, expect=403)
    assert r.json() == {"detail": "Không có quyền thao tác resource này"}
    assert (await lg.call("DELETE", f"{RES}/{res['id']}", u)).json() == {"ok": True}
    r = await lg.call("DELETE", f"{RES}/{res['id']}", u, expect=404)
    assert r.json() == {"detail": "Không tìm thấy resource"}
    assert (await lg.call("GET", RES, u)).json() == {"items": []}
    row = await lg.canon(res["id"])
    assert row["removed"] is True   # lịch sử giữ lại
    assert await env.scalar("SELECT count(*) FROM legacy_ingest.resource_assignments") == 0
    removed = (await canonical(env, "resources"))["items"]
    assert [i["removed"] for i in removed if i["id"] == res["id"]] == [True]


# ── Dữ liệu legacy vào CÙNG read model canonical ─────────────────────────────────────────

async def test_legacy_resource_is_exported_like_a_tool_resource(lg, env):
    u = await lg.user()
    src = await lg.source(u)
    web = await lg.resource(u, src, "same-ext", display_name="Trang A", username="usr", country="Brazil")
    tool = await env.tool(user=await env.verified_user())
    r = await tool.post("/v1/integrations/resources/batch", {"items": [
        {"platform": "facebook", "resource_type": "unspecified", "external_id": "same-ext", "display_name": "Trang A",
         "username": "usr", "country": {"name": "Brazil"}, "observed_at": ts(0)}]})
    assert r.status_code == 200
    items = {i["owner_account_id"]: i for i in (await canonical(env, "resources"))["items"]}
    mine, theirs = items[u["sub"]], items[tool.owner]
    shared = ("platform", "resource_type", "external_id", "removed", "state", "display_name", "username", "country")
    assert {k: mine[k] for k in shared} == {k: theirs[k] for k in shared}   # Analytics không phân biệt hai đường vào
    assert mine["origin"] == "web_manual" and theirs["origin"] == "tool" and mine["execution_mode"] == "fallback_tool"
    assert mine["extensions"]["legacy"]["input_ref"] == "same-ext"


async def test_legacy_edits_reach_the_feed_with_new_revisions(lg, env):
    u = await lg.user()
    res = await lg.resource(u, await lg.source(u), "feedme")
    await lg.call("PATCH", f"{RES}/{res['id']}", u, json={"display_name": "Sửa"})
    await lg.call("DELETE", f"{RES}/{res['id']}", u)
    r = await env.http.get("/internal/v1/feed", params={"after": 0, "limit": 100},
                           headers={"X-Service-Key": __import__("conftest").FEED_KEY_ANALYTICS})
    evs = [e for e in r.json()["events"] if e["type"] == "resource.changed"]
    assert len(evs) == 3 and [e["data"]["revision"] for e in evs] == sorted(e["data"]["revision"] for e in evs)


async def test_legacy_source_project_key_is_kept_as_an_inert_field(lg, env):
    """`project_key` của Source là trường legacy (hợp đồng đóng băng): vẫn lưu/trả lại nhưng KHÔNG sinh project canonical."""
    u = await lg.user()
    src = await lg.source(u, project_key="ITL")
    res = await lg.resource(u, src, "p")
    await lg.call("PATCH", f"{RES}/sources/{src['id']}", u, json={"project_key": "Sible Pro"})
    got = (await lg.call("GET", f"{RES}/sources", u)).json()["items"][0]
    assert got["project_key"] == "Sible Pro"
    assert await env.scalar("SELECT count(*) FROM projects") == 0
    assert await env.scalar("SELECT count(*) FROM resource_project_bindings WHERE resource_id = $1", res["id"]) == 0


# ── /v1/ops ───────────────────────────────────────────────────────────────────────────

@covers("GET /v1/ops/inventory", "GET /v1/ops/inventory/revision", "GET /v1/ops/users", "GET /v1/ops/sources")
async def test_ops_inventory_sees_everyone_with_owner_username(lg, env):
    a, b = await lg.user(username="alice"), await lg.user(username="bob")
    async with env.db.connection() as conn:
        await conn.execute("UPDATE account_projection SET username = 'alice' WHERE account_id = $1", a["sub"])
        await conn.execute("UPDATE account_projection SET username = 'bob' WHERE account_id = $1", b["sub"])
    await lg.resource(a, await lg.source(a), "ra", display_name="RA")
    await lg.resource(b, await lg.source(b), "rb", display_name="RB")
    ops = await lg.user("dash_admin")
    inv = (await lg.call("GET", f"{OPS}/inventory", ops)).json()["items"]
    assert {(i["display_name"], i["owner_username"]) for i in inv} == {("RA", "alice"), ("RB", "bob")}
    assert all(set(i) == RESOURCE_KEYS | {"source_ids", "owner_username"} for i in inv)
    only = (await lg.call("GET", f"{OPS}/inventory", ops, params={"owner_account_id": a["sub"]})).json()["items"]
    assert [i["display_name"] for i in only] == ["RA"]
    users = (await lg.call("GET", f"{OPS}/users", ops)).json()["items"]
    assert users == [{"id": a["sub"], "username": "alice"}, {"id": b["sub"], "username": "bob"}]
    sources = (await lg.call("GET", f"{OPS}/sources", ops)).json()["items"]
    assert len(sources) == 2 and all(set(s) == SOURCE_KEYS | {"owner_username"} for s in sources)
    assert len((await lg.call("GET", f"{OPS}/sources", ops, params={"owner_account_id": b["sub"]})).json()["items"]) == 1
    rev1 = (await lg.call("GET", f"{OPS}/inventory/revision", ops)).json()
    assert set(rev1) == {"revision"} and rev1["revision"].endswith(".2")
    res = await lg.resource(a, (await lg.call("GET", f"{RES}/sources", a)).json()["items"][0], "rc")
    env.clock.advance(seconds=5)
    await lg.call("PATCH", f"{RES}/{res['id']}", a, json={"display_name": "đổi"})
    rev2 = (await lg.call("GET", f"{OPS}/inventory/revision", ops)).json()["revision"]
    assert rev2.endswith(".3") and rev2 != rev1["revision"]
    own = (await lg.call("GET", f"{OPS}/inventory/revision", ops, params={"owner_account_id": b["sub"]})).json()["revision"]
    assert own.endswith(".1")


@covers("GET /v1/ops/platforms", "GET /v1/ops/platform-policies", "PATCH /v1/ops/platform-policies/{platform}")
async def test_platform_policies_read_and_admin_partial_update(lg):
    ops, admin = await lg.user("dash_admin"), await lg.user("admin")
    listed = (await lg.call("GET", f"{OPS}/platforms", ops)).json()["items"]
    assert [p["platform"] for p in listed] == ["facebook", "instagram", "tiktok", "youtube"]
    assert all(set(p) == POLICY_KEYS for p in listed)
    assert (await lg.call("GET", f"{OPS}/platform-policies", ops)).json()["items"] == listed
    r = await lg.call("PATCH", f"{OPS}/platform-policies/instagram", ops, expect=403, json={"fallback_enabled": True})
    assert r.json() == {"detail": "Chỉ admin được thao tác này"}
    out = (await lg.call("PATCH", f"{OPS}/platform-policies/instagram", admin, json={"fallback_enabled": True})).json()
    assert set(out) == POLICY_KEYS and out["fallback_enabled"] is True and out["web_entry_enabled"] is True
    assert out["adapter_status"] == "none" and out["updated_by"] == admin["sub"]
    r = await lg.call("PATCH", f"{OPS}/platform-policies/threads", admin, expect=404, json={"adapter_status": "planned"})
    assert "chưa được tạo" in r.json()["detail"]   # nền tảng phải có trong Platform Registry trước
    await lg.call("PUT", "/v1/integrations/admin/platforms/threads", admin, json={"display_name": "Threads"})
    new = (await lg.call("PATCH", f"{OPS}/platform-policies/threads", admin, json={"adapter_status": "planned"})).json()
    assert new["platform"] == "threads" and new["web_entry_enabled"] is True and new["fallback_enabled"] is True
    r = await lg.call("PATCH", f"{OPS}/platform-policies/threads", admin, expect=422, json={"adapter_status": "gibberish"})
    assert r.json()["detail"][0]["loc"] == ["body", "adapter_status"]
    r = await lg.call("PATCH", f"{OPS}/platform-policies/Bad Name", admin, expect=400, json={})
    assert "Platform phải dạng" in r.json()["detail"]


@covers("PATCH /v1/ops/resources/{resource_id}/execution-mode")
async def test_admin_sets_execution_mode_canonically(lg, env):
    u, ops, admin = await lg.user(), await lg.user("dash_admin"), await lg.user("admin")
    res = await lg.resource(u, await lg.source(u), "mode-me")
    r = await lg.call("PATCH", f"{OPS}/resources/{res['id']}/execution-mode", ops, expect=403,
                      json={"execution_mode": "disabled"})
    assert r.json() == {"detail": "Chỉ admin được thao tác này"}
    r = await lg.call("PATCH", f"{OPS}/resources/{res['id']}/execution-mode", admin, expect=422,
                      json={"execution_mode": "fallback_tool"})   # tên canonical KHÔNG hợp lệ ở API legacy
    assert r.json()["detail"][0]["loc"] == ["body", "execution_mode"]
    r = await lg.call("PATCH", f"{OPS}/resources/res_none/execution-mode", admin, expect=404,
                      json={"execution_mode": "disabled"})
    assert r.json() == {"detail": "Không tìm thấy resource"}
    before = (await lg.canon(res["id"]))["revision"]
    assert (await lg.call("PATCH", f"{OPS}/resources/{res['id']}/execution-mode", admin,
                          json={"execution_mode": "primary_tool"})).json() == {"ok": True}
    row = await lg.canon(res["id"])
    assert row["execution_mode"] == "primary_tool" and row["revision"] > before and row["owner_account_id"] == u["sub"]
    assert (await lg.call("GET", RES, u)).json()["items"][0]["execution_mode"] == "primary_tool"
    audit = await env.rows("SELECT event_type, account_id FROM legacy_ingest.operation_audit_log "
                           "WHERE event_type = 'execution_mode_changed'")
    assert audit == [{"event_type": "execution_mode_changed", "account_id": admin["sub"]}]


async def test_owner_switches_execution_mode_and_jobs_follow(lg, env):
    """Owner chuyển fallback_local→primary_tool: job bị cancel.
    Owner chuyển primary_tool→fallback_local: collect-job được tạo lại."""
    # Tạo user có capability:manual_channel_mgmt
    u = lg.env.user(perms=["capability:manual_channel_mgmt"])
    other = await lg.user()
    await lg.env.projection(u["sub"])
    await lg.env.projection(other["sub"])
    src = await lg.source(u)
    res = await lg.resource(u, src, "https://fb.com/switch-me")
    assert res["execution_mode"] == "fallback_local"

    # Bước 1: owner chuyển → primary_tool → collect-job bị cancel
    assert await env.scalar("SELECT count(*) FROM jobs WHERE status='pending'") == 1
    r = await lg.call("PATCH", f"{RES}/{res['id']}/execution-mode", u,
                      json={"execution_mode": "primary_tool"})
    assert r.json() == {"ok": True}
    assert await env.scalar("SELECT count(*) FROM jobs WHERE status='pending'") == 0
    assert await env.scalar("SELECT count(*) FROM jobs WHERE status='cancelled'") == 1
    assert (await lg.call("GET", RES, u)).json()["items"][0]["execution_mode"] == "primary_tool"

    # Bước 2: owner chuyển ngược lại → fallback_local → collect-job được tạo mới
    r = await lg.call("PATCH", f"{RES}/{res['id']}/execution-mode", u,
                      json={"execution_mode": "fallback_local"})
    assert r.json() == {"ok": True}
    assert await env.scalar("SELECT count(*) FROM jobs WHERE status='pending'") == 1

    # Non-owner bị từ chối 403
    r = await lg.call("PATCH", f"{RES}/{res['id']}/execution-mode", other, expect=403,
                      json={"execution_mode": "disabled"})
    # Shape lỗi giữ nguyên legacy {detail: ...}
    assert "detail" in r.json()


# ── Run Queue ↔ jobs ──────────────────────────────────────────────────────────────────

@covers("POST /v1/ops/batches", "GET /v1/ops/batches", "GET /v1/ops/batches/{batch_id}")
async def test_batches_create_list_get(lg):
    ops, admin = await lg.user("dash_admin"), await lg.user("admin")
    b1 = (await lg.call("POST", f"{OPS}/batches", ops, json={"platform": "facebook", "name": " Đợt 1 "})).json()
    assert set(b1) == {"id", "created_by", "platform", "name", "status", "created_at", "updated_at"}
    assert b1["status"] == "open" and b1["name"] == "Đợt 1" and b1["created_by"] == ops["sub"]
    b2 = (await lg.call("POST", f"{OPS}/batches", admin, json={"platform": "youtube"})).json()
    allb = (await lg.call("GET", f"{OPS}/batches", ops)).json()["items"]
    assert [b["id"] for b in allb] == [b2["id"], b1["id"]]
    mine = (await lg.call("GET", f"{OPS}/batches", ops, params={"mine_only": "true"})).json()["items"]
    assert [b["id"] for b in mine] == [b1["id"]]
    one = (await lg.call("GET", f"{OPS}/batches/{b1['id']}", ops)).json()
    assert one == {**b1, "items": []}
    r = await lg.call("GET", f"{OPS}/batches/none", ops, expect=404)
    assert r.json() == {"detail": "Không tìm thấy batch"}
    r = await lg.call("POST", f"{OPS}/batches", ops, expect=422, json={"name": "thiếu platform"})
    assert r.json()["detail"][0]["loc"] == ["body", "platform"]


async def _queue_world(lg, env, *, adapter: str):
    u, ops, admin = await lg.user(), await lg.user("dash_admin"), await lg.user("admin")
    await lg.call("PATCH", f"{OPS}/platform-policies/facebook", admin, json={"adapter_status": adapter})
    src = await lg.source(u)
    res = await lg.resource(u, src, "queue-me")
    batch = (await lg.call("POST", f"{OPS}/batches", ops, json={"platform": "facebook"})).json()
    return u, ops, admin, res, batch


@covers("POST /v1/ops/batches/{batch_id}/items", "PATCH /v1/ops/items/{item_id}/cancel")
async def test_add_items_skip_reasons_and_blocked_without_adapter(lg, env):
    u, ops, admin, res, batch = await _queue_world(lg, env, adapter="none")
    yt = await lg.resource(u, await lg.source(u, platform="youtube", name="yt"), "yt-chan", platform="youtube")
    ig = await lg.resource(u, await lg.source(u, platform="instagram", name="ig"), "ig-user", platform="instagram")
    body = {"resource_ids": [res["id"], "res_none", yt["id"], ig["id"]]}
    out = (await lg.call("POST", f"{OPS}/batches/{batch['id']}/items", ops, json=body)).json()
    assert set(out) == {"added", "skipped"} and len(out["added"]) == 1
    assert {(s["resource_id"], s["reason"]) for s in out["skipped"]} == {
        ("res_none", "resource_not_found"), (yt["id"], "platform_mismatch"), (ig["id"], "platform_mismatch")}
    detail = (await lg.call("GET", f"{OPS}/batches/{batch['id']}", ops)).json()
    item = detail["items"][0]
    assert set(item) == ITEM_KEYS and item["status"] == "blocked_no_adapter" and item["owner_account_id"] == u["sub"]
    assert item["resource_id"] == res["id"] and item["attempts"] == 0 and item["source_assignment_id"]
    assert await env.scalar("SELECT count(*) FROM jobs") == 0   # chưa có adapter: KHÔNG giả lập thành công, không có job
    ig_batch = (await lg.call("POST", f"{OPS}/batches", ops, json={"platform": "instagram"})).json()
    out = (await lg.call("POST", f"{OPS}/batches/{ig_batch['id']}/items", ops, json={"resource_ids": [ig["id"]]})).json()
    assert out == {"added": [], "skipped": [{"resource_id": ig["id"], "reason": "execution_mode_not_fallback_local"}]}
    await lg.call("PATCH", f"{OPS}/platform-policies/facebook", admin, json={"fallback_enabled": False})
    b_off = (await lg.call("POST", f"{OPS}/batches", ops, json={"platform": "facebook"})).json()
    out = (await lg.call("POST", f"{OPS}/batches/{b_off['id']}/items", ops, json={"resource_ids": [res["id"]]})).json()
    assert out["skipped"] == [{"resource_id": res["id"], "reason": "platform_fallback_disabled"}]
    await lg.call("PATCH", f"{OPS}/platform-policies/facebook", admin, json={"fallback_enabled": True})
    await lg.call("PATCH", f"{OPS}/resources/{res['id']}/execution-mode", admin, json={"execution_mode": "disabled"})
    out = (await lg.call("POST", f"{OPS}/batches/none/items", ops, expect=404, json=body)).json()
    assert out == {"detail": "Không tìm thấy batch"}
    batch2 = (await lg.call("POST", f"{OPS}/batches", ops, json={"platform": "facebook"})).json()
    out = (await lg.call("POST", f"{OPS}/batches/{batch2['id']}/items", ops, json={"resource_ids": [res["id"]]})).json()
    assert out["skipped"][0]["reason"] == "execution_mode_not_fallback_local"
    r = await lg.call("PATCH", f"{OPS}/items/{item['id']}/cancel", ops)
    assert r.json() == {"ok": True}
    r = await lg.call("PATCH", f"{OPS}/items/{item['id']}/cancel", ops, expect=400)
    assert r.json() == {"detail": "Item không tồn tại hoặc đã ở trạng thái cuối (success/failed/cancelled)"}
    r = await lg.call("PATCH", f"{OPS}/items/none/cancel", ops, expect=400)
    assert r.status_code == 400


async def test_queued_item_is_a_canonical_job_and_status_follows_the_job(lg, env):
    u, ops, admin, res, batch = await _queue_world(lg, env, adapter="ready")
    from integration_service.clients import policy
    from integration_service.clients.policy import SYSTEM_SCOPE
    tool = await env.tool(kind="fallback_tool", user=u, handshake=False,
                          scopes=policy.default_scopes("fallback_tool") + [SYSTEM_SCOPE])
    hs = await env.handshake(tool.api_key, platforms=["facebook"])
    tool.installation_id, tool.device_id = hs["installation_id"], hs["device_id"]
    ins = tool.installation_id
    out = (await lg.call("POST", f"{OPS}/batches/{batch['id']}/items", ops, json={"resource_ids": [res["id"]]})).json()
    item_id = out["added"][0]
    job = (await env.rows("SELECT * FROM jobs"))[0]
    assert job["resource_id"] == res["id"] and job["owner_account_id"] == u["sub"] and job["created_by"] == ops["sub"]
    status = lambda: lg.call("GET", f"{OPS}/batches/{batch['id']}", ops)  # noqa: E731
    assert (await status()).json()["items"][0]["status"] == "queued"
    got = await claim(tool, ins)
    assert [j["job_id"] for j in got] == [job["id"]]
    it = (await status()).json()["items"][0]
    assert it["status"] == "running" and it["attempts"] == 1
    r = await tool.post(f"/v1/integrations/jobs/{job['id']}/fail", {"error_code": "NET", "message": "hết giờ", "retryable": False})
    assert r.json()["data"]["status"] == "failed"
    it = (await status()).json()["items"][0]
    assert it["status"] == "failed" and it["last_error"] == "hết giờ"
    r = await lg.call("PATCH", f"{OPS}/items/{item_id}/cancel", ops, expect=400)   # trạng thái cuối
    assert "trạng thái cuối" in r.json()["detail"]
    # lượt khác: thành công
    b2 = (await lg.call("POST", f"{OPS}/batches", ops, json={"platform": "facebook"})).json()
    i2 = (await lg.call("POST", f"{OPS}/batches/{b2['id']}/items", ops, json={"resource_ids": [res["id"]]})).json()["added"][0]
    await claim(tool, ins)
    job2 = (await env.rows("SELECT id FROM jobs WHERE status = 'leased'"))[0]["id"]
    await tool.post(f"/v1/integrations/jobs/{job2}/complete", {"result": {}})
    assert (await lg.call("GET", f"{OPS}/batches/{b2['id']}", ops)).json()["items"][0]["status"] == "success"
    # huỷ khi đang chờ: huỷ cả job
    b3 = (await lg.call("POST", f"{OPS}/batches", ops, json={"platform": "facebook"})).json()
    i3 = (await lg.call("POST", f"{OPS}/batches/{b3['id']}/items", ops, json={"resource_ids": [res["id"]]})).json()["added"][0]
    assert (await lg.call("PATCH", f"{OPS}/items/{i3}/cancel", ops)).json() == {"ok": True}
    assert await claim(tool, ins) == []
    assert (await env.scalar("SELECT count(*) FROM jobs WHERE status = 'cancelled'")) == 1
    assert i2 and (await lg.call("GET", f"{OPS}/batches/{b3['id']}", ops)).json()["items"][0]["status"] == "cancelled"


async def test_deleting_or_unfalling_a_resource_cancels_its_pending_jobs(lg, env):
    u, ops, admin, res, batch = await _queue_world(lg, env, adapter="ready")
    await lg.call("POST", f"{OPS}/batches/{batch['id']}/items", ops, json={"resource_ids": [res["id"]]})
    await lg.call("PATCH", f"{OPS}/resources/{res['id']}/execution-mode", admin, json={"execution_mode": "disabled"})
    assert await env.scalar("SELECT status FROM jobs") == "cancelled"


# ── /v1/viewer/links (pairing) ───────────────────────────────────────────────────────────

@covers("POST /v1/viewer/links/code", "GET /v1/viewer/links", "DELETE /v1/viewer/links/{tool_user_id}",
        "POST /v1/viewer/links/redeem")
async def test_pairing_flow(lg, env):
    u, other = await lg.user(), await lg.user()
    code = (await lg.call("POST", f"{LNK}/code", u)).json()
    assert set(code) == {"code", "expires_at"} and len(code["code"]) == 8 and code["code"] == code["code"].upper()
    assert code["expires_at"] == "2026-10-05T10:15:00+00:00"
    tool_user = "tool-user-" + uuid.uuid4().hex[:8]
    r = await lg.call("POST", f"{LNK}/redeem", None, json={"code": code["code"].lower(), "tool_user_id": tool_user})
    assert r.json() == {"ok": True}   # công khai, không cần JWT; mã không phân biệt hoa/thường
    again = await lg.call("POST", f"{LNK}/redeem", None, expect=400, json={"code": code["code"], "tool_user_id": tool_user})
    assert again.json() == {"detail": "Mã không hợp lệ hoặc đã hết hạn"}
    links = (await lg.call("GET", LNK, u)).json()["items"]
    assert len(links) == 1 and set(links[0]) == {"tool_user_id", "label", "status", "linked_at"}
    assert links[0]["tool_user_id"] == tool_user and links[0]["status"] == "active"
    assert (await lg.call("GET", LNK, other)).json() == {"items": []}
    r = await lg.call("DELETE", f"{LNK}/{tool_user}", other, expect=404)
    assert r.json() == {"detail": "Không tìm thấy liên kết"}
    assert (await lg.call("DELETE", f"{LNK}/{tool_user}", u)).json() == {"ok": True}
    await lg.call("DELETE", f"{LNK}/{tool_user}", u, expect=404)
    assert (await lg.call("GET", LNK, u)).json() == {"items": []}


async def test_redeem_expired_unknown_and_validation(lg, env):
    u = await lg.user()
    code = (await lg.call("POST", f"{LNK}/code", u)).json()["code"]
    env.clock.advance(minutes=16)
    r = await lg.call("POST", f"{LNK}/redeem", None, expect=400, json={"code": code, "tool_user_id": "tool-user-12345"})
    assert r.json() == {"detail": "Mã không hợp lệ hoặc đã hết hạn"}
    assert await env.scalar("SELECT status FROM legacy_ingest.link_codes") == "expired"
    r = await lg.call("POST", f"{LNK}/redeem", None, expect=400, json={"code": "ZZZZZZZZ", "tool_user_id": "tool-user-12345"})
    assert r.status_code == 400
    r = await lg.call("POST", f"{LNK}/redeem", None, expect=422, json={"code": "AB", "tool_user_id": "short"})
    assert {tuple(d["loc"]) for d in r.json()["detail"]} == {("body", "code"), ("body", "tool_user_id")}
    assert await env.scalar("SELECT count(*) FROM legacy_ingest.account_links") == 0


async def test_redeem_is_rate_limited_per_ip(make_env):
    e2, _ = await make_env(legacy_redeem_per_window=3)
    for _ in range(3):
        assert (await e2.http.post(f"{LNK}/redeem", json={"code": "ZZZZZZZZ", "tool_user_id": "tool-user-12345"})).status_code == 400
    r = await e2.http.post(f"{LNK}/redeem", json={"code": "ZZZZZZZZ", "tool_user_id": "tool-user-12345"})
    assert r.status_code == 429 and r.json()["detail"].startswith("Quá nhiều lần thử") and int(r.headers["Retry-After"]) > 0


# ── Lỗi hạ tầng ──────────────────────────────────────────────────────────────────────────

async def test_legacy_write_is_atomic_when_the_database_blips_midway(lg, env, monkeypatch):
    from integration_service.ingest import merge
    u = await lg.user()
    src = await lg.source(u)

    async def boom(*a, **kw):
        raise asyncpg.InterfaceError("kết nối chập chờn")

    monkeypatch.setattr(merge, "emit_resource_changes", boom)
    body = {"source_instance_id": src["id"], "platform": "facebook", "input_ref": "half"}
    r = await lg.call("POST", RES, u, expect=503, json=body)
    assert r.json() == {"detail": "Dịch vụ tạm thời không khả dụng"}
    assert await env.scalar("SELECT count(*) FROM resources") == 0
    assert await env.scalar("SELECT count(*) FROM legacy_ingest.resource_assignments") == 0
    monkeypatch.undo()
    assert (await lg.call("POST", RES, u, json=body)).status_code == 200


async def test_import_is_all_or_nothing(lg, env, monkeypatch):
    from integration_service.legacy_acl import service as svc
    u = await lg.user()
    src = await lg.source(u)
    real = svc.LegacyService._create_one
    calls = {"n": 0}

    async def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise asyncpg.InterfaceError("mất kết nối")
        return await real(self, *a, **kw)

    monkeypatch.setattr(svc.LegacyService, "_create_one", flaky)
    body = {"source_instance_id": src["id"], "platform": "facebook", "lines": ["a", "b", "c", "d"]}
    await lg.call("POST", f"{RES}/import", u, expect=503, json=body)
    assert await env.scalar("SELECT count(*) FROM resources") == 0 and await env.scalar(
        "SELECT count(*) FROM outbox WHERE type = 'resource.changed'") == 0
    monkeypatch.undo()
    assert (await lg.call("POST", f"{RES}/import", u, json=body)).json()["created_count"] == 4


# ── Tool tiếp quản resource nhập tay ────────────────────────────────────────────────────

async def test_tool_push_takes_over_a_manually_entered_resource(lg, env):
    """Nhập tay (origin web_manual) rồi Tool đẩy CÙNG khoá: Tool tiếp quản (origin='tool'). Legacy không còn thấy/sửa nó
    (404), dữ liệu Tool cung cấp (extensions/managed/active) nguyên vẹn; Legacy không tạo đè (409)."""
    u = await lg.user()
    src = await lg.source(u)
    web = await lg.resource(u, src, "takeover-ext", display_name="Tay")
    before = await lg.canon(web["id"])
    assert before["origin"] == "web_manual"
    tool = await env.tool(user=u)
    r = await tool.post("/v1/integrations/resources/batch", {"items": [{
        "platform": "facebook", "resource_type": before["resource_type"], "external_id": before["external_id"],
        "display_name": "Do Tool", "observed_at": ts(5), "managed": True, "active": True,
        "extensions": {"facebook": {"fans": 12}}}]})
    assert r.status_code == 200, r.text
    after = await lg.canon(web["id"])
    assert after["origin"] == "tool"
    # Legacy: không còn thấy trong danh sách, PATCH/DELETE bị 404, tạo đè bị 409
    assert (await lg.call("GET", RES, u)).json() == {"items": []}
    r = await lg.call("PATCH", f"{RES}/{web['id']}", u, expect=404, json={"display_name": "Tay sua"})
    assert r.json() == {"detail": "Không tìm thấy resource"}
    await lg.call("DELETE", f"{RES}/{web['id']}", u, expect=404)
    await lg.call("POST", RES, u, expect=409, json={"source_instance_id": src["id"], "platform": "facebook",
                                                    "input_ref": "takeover-ext"})
    # Dữ liệu Tool nguyên vẹn
    item = [i for i in (await canonical(env, "resources"))["items"] if i["id"] == web["id"]][0]
    assert item["removed"] is False and item["origin"] == "tool" and item["display_name"] == "Do Tool"
    assert item["managed"] is True and item["active"] is True
    assert item["extensions"].get("facebook") == {"fans": 12} and "project_keys" not in item


async def test_tool_push_with_identical_data_still_takes_over(lg, env):
    """Tool đẩy đúng dữ liệu đang có (verdict 'unchanged') vẫn phải tiếp quản, không để Legacy còn sửa được."""
    u = await lg.user()
    web = await lg.resource(u, await lg.source(u), "same-data")
    row = await lg.canon(web["id"])
    tool = await env.tool(user=u)
    payload = {"platform": "facebook", "resource_type": row["resource_type"], "external_id": row["external_id"],
               "observed_at": ts(5)}
    # đưa dữ liệu hiện có (kể cả extensions.legacy) y nguyên qua đường Tool
    payload.update(display_name=row["data"]["display_name"], state=row["state"],
                   extensions=row["data"].get("extensions", {}))
    for k in ("username", "url", "country", "managed", "active"):
        if row["data"].get(k) is not None:
            payload[k] = row["data"][k]
    r = await tool.post("/v1/integrations/resources/batch", {"items": [payload]})
    assert r.status_code == 200, r.text
    assert (await lg.canon(web["id"]))["origin"] == "tool"
    await lg.call("PATCH", f"{RES}/{web['id']}", u, expect=404, json={"display_name": "x"})
