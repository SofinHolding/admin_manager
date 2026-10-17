"""Nền tảng do Admin/dash_admin tạo (Platform Registry = nguồn duy nhất) và ánh xạ nền tảng/kênh khi Tool dùng chung đẩy hộ."""

from __future__ import annotations

import pytest
from conftest import ts
from helpers import claim
from integration_service.clients import policy

pytestmark = pytest.mark.needs_pg
ADMIN_PLATFORMS = "/v1/integrations/admin/platforms"
OPS, RES = "/v1/ops", "/v1/resources"


async def _user(env, role="viewer", **claims):
    u = env.user(role=role, **claims)
    await env.projection(u["sub"])
    return u


async def _put(env, u, key, *, expect=200, **body):
    r = await env.http.put(f"{ADMIN_PLATFORMS}/{key}", headers=u["headers"],
                           json={"display_name": body.pop("display_name", key.title()), "enabled": True, **body})
    assert r.status_code == expect, r.text
    return r


async def test_dash_admin_creates_platform_but_viewer_cannot(env):
    dash, viewer = await _user(env, "dash_admin"), await _user(env)
    await _put(env, viewer, "threads", expect=403)
    r = await _put(env, dash, "threads", display_name="Threads")
    assert r.json()["data"] == {"key": "threads", "display_name": "Threads", "enabled": True, "description": None,
                                "aliases": []}
    # metric registry vẫn chỉ admin thật
    r = await env.http.put("/v1/integrations/admin/metrics/saves", headers=dash["headers"], json={"display_name": "S"})
    assert r.status_code == 403


async def test_new_platform_is_available_for_web_entry_and_its_channels_go_to_the_shared_collector(env):
    dash = await _user(env, "dash_admin")
    viewer = env.user(perms=["capability:manual_channel_mgmt"])
    await env.projection(viewer["sub"])
    assert "threads" not in [p["platform"] for p in (await env.http.get(f"{RES}/platforms", headers=viewer["headers"])).json()["items"]]
    await _put(env, dash, "threads", display_name="Threads")
    items = (await env.http.get(f"{RES}/platforms", headers=viewer["headers"])).json()["items"]
    assert {"platform": "threads", "display_name": "Threads"} in items
    policy_row = next(p for p in (await env.http.get(f"{OPS}/platform-policies", headers=dash["headers"])).json()["items"]
                      if p["platform"] == "threads")
    # mặc định: nhập tay được VÀ kênh được giao cho Tool dùng chung kéo số liệu (không cần admin bật gì thêm)
    assert policy_row["web_entry_enabled"] is True and policy_row["fallback_enabled"] is True
    assert policy_row["display_name"] == "Threads"
    src = (await env.http.post(f"{RES}/sources", headers=viewer["headers"],
                               json={"name": "Th", "platform": "threads"})).json()
    r = await env.http.post(RES, headers=viewer["headers"], json={
        "source_instance_id": src["id"], "platform": "threads", "input_ref": "https://threads.net/@a"})
    assert r.status_code == 200 and r.json()["platform"] == "threads" and r.json()["execution_mode"] == "fallback_local"
    jobs = await env.rows("SELECT platform, kind, status FROM jobs")
    assert [(j["platform"], j["kind"], j["status"]) for j in jobs] == [("threads", "collect", "pending")]


async def test_disabled_or_unknown_platform_cannot_be_used_for_web_entry(env):
    dash, admin, viewer = await _user(env, "dash_admin"), await _user(env, "admin"), await _user(env)
    await _put(env, dash, "threads", enabled=False)
    r = await env.http.post(f"{RES}/sources", headers=viewer["headers"], json={"name": "x", "platform": "threads"})
    assert r.status_code == 400
    r = await env.http.post(f"{RES}/sources", headers=viewer["headers"], json={"name": "x", "platform": "nope"})
    assert r.status_code == 400
    r = await env.http.patch(f"{OPS}/platform-policies/nope", headers=admin["headers"], json={"fallback_enabled": True})
    assert r.status_code == 404                      # policy chỉ chỉnh hành vi của nền tảng ĐÃ có trong registry
    await _put(env, dash, "threads", enabled=True)
    r = await env.http.patch(f"{OPS}/platform-policies/threads", headers=admin["headers"], json={"fallback_enabled": False})
    assert r.status_code == 200 and r.json()["fallback_enabled"] is False and r.json()["display_name"] == "Threads"


async def test_personal_tool_gets_new_platforms_automatically_shared_tool_does_not(env):
    dash = await _user(env, "dash_admin")
    personal = await env.tool(platforms=("facebook",))
    shared = await env.tool(kind="fallback_tool", platforms=("facebook",), handshake=False)
    await _put(env, dash, "threads", display_name="Threads")
    mine = {p["key"] for p in (await personal.get("/v1/integrations/platforms")).json()["data"]}
    assert mine == {"facebook", "instagram", "threads", "tiktok", "youtube"}
    r = await personal.post("/v1/integrations/snapshots", {"platform": "threads", "snapshot_type": "delta",
                                                           "observed_at": ts(), "report_timezone": "UTC",
                                                           "expected_counts": {}}, idem=True)
    assert r.status_code == 201, r.text                # Tool cá nhân đẩy được lên nền tảng Admin vừa tạo
    # Tool dùng chung: chỉ nền tảng admin đã chọn cho client; nền tảng mới KHÔNG tự cấp
    r = await shared.get("/v1/integrations/platforms")
    assert r.json()["data"] == [{"key": "facebook", "display_name": "Facebook", "enabled": True, "description": None,
                                 "aliases": [], "codes": ["facebook"]}]
    await _put(env, dash, "facebook", display_name="Facebook", enabled=False)
    assert (await shared.get("/v1/integrations/platforms")).json()["data"] == []


# ── Tool dùng chung: ánh xạ đúng kênh + đúng nền tảng ─────────────────────────────────────────

async def _collector_job(env, lg_user, platform="tiktok", ref="https://tiktok.com/@kenh1"):
    """User nhập tay kênh (capability) -> job collect tự tạo -> Tool dùng chung claim được job đó."""
    src = (await env.http.post(f"{RES}/sources", headers=lg_user["headers"], json={"name": "S", "platform": platform})).json()
    res = (await env.http.post(RES, headers=lg_user["headers"], json={
        "source_instance_id": src["id"], "platform": platform, "input_ref": ref})).json()
    tool = await env.tool(kind="fallback_tool", platforms=(platform, "facebook"), handshake=False,
                          scopes=[*policy.default_scopes("fallback_tool"), policy.SYSTEM_SCOPE])
    hs = await env.handshake(tool.api_key, platforms=[platform], capabilities=["data.collect"])
    tool.installation_id = hs["installation_id"]
    jobs = await claim(tool, tool.installation_id, platforms=[platform], capabilities=["data.collect"])
    assert len(jobs) == 1 and jobs[0]["platform"] == platform
    return tool, jobs[0], res


def _snapshot_body(platform, **over):
    return {"platform": platform, "snapshot_type": "delta", "observed_at": ts(), "report_timezone": "UTC",
            "expected_counts": {"content": 1}, **over}


def _item(ext, resource_ext, views=10):
    return {"external_id": ext, "resource_external_id": resource_ext, "published_date": "2026-10-04",
            "metrics": {"views": views}, "observed_at": ts()}


async def test_shared_tool_data_lands_on_the_users_channel_and_platform(env):
    viewer = env.user(perms=["capability:manual_channel_mgmt"])
    await env.projection(viewer["sub"])
    tool, job, res = await _collector_job(env, viewer)
    ext = job["resource"]["external_id"]
    hdr = {"X-Delegated-Job": job["job_id"]}
    r = await tool.post("/v1/integrations/snapshots", _snapshot_body("tiktok"), headers=hdr, idem=True)
    assert r.status_code == 201, r.text
    sid = r.json()["data"]["snapshot_id"]
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/content", {"items": [_item("v1", ext, 42)]}, headers=hdr)
    assert r.status_code == 200 and r.json()["data"]["rejected"] == 0, r.text
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/commit", {}, headers=hdr)
    assert r.status_code == 200, r.text
    row = (await env.rows("SELECT owner_account_id, platform, resource_id, data FROM content_items WHERE external_id = 'v1'"))[0]
    assert row["owner_account_id"] == viewer["sub"]          # owner = chủ kênh, không phải chủ key dùng chung
    assert row["platform"] == "tiktok" and row["resource_id"] == res["id"]
    assert row["data"]["metrics"]["views"] == 42


async def test_delegated_ingest_cannot_cross_platform_type_resource_or_create_resources(env):
    viewer = env.user(perms=["capability:manual_channel_mgmt"])
    await env.projection(viewer["sub"])
    tool, job, _ = await _collector_job(env, viewer)
    hdr = {"X-Delegated-Job": job["job_id"]}

    # khác nền tảng của job (dù key được phép facebook)
    r = await tool.post("/v1/integrations/snapshots", _snapshot_body("facebook"), headers=hdr, idem=True)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    # full snapshot (sẽ đánh dấu removed) bị cấm
    r = await tool.post("/v1/integrations/snapshots", _snapshot_body("tiktok", snapshot_type="full"), headers=hdr, idem=True)
    assert r.status_code == 422
    sid = (await tool.post("/v1/integrations/snapshots", _snapshot_body("tiktok", expected_counts={"content": 1}),
                           headers=hdr, idem=True)).json()["data"]["snapshot_id"]
    # không tạo/sửa resource
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/resources", {"items": [
        {"external_id": "x", "resource_type": "page", "observed_at": ts()}]}, headers=hdr)
    assert r.status_code == 403
    # số liệu của kênh KHÁC (cùng owner/nền tảng) bị từ chối từng bản ghi
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/content", {"items": [_item("v9", "other-channel")]}, headers=hdr)
    data = r.json()["data"]
    assert r.status_code == 200 and data["rejected"] == 1 and data["rejections"][0]["code"] == "RESOURCE_UNKNOWN"
    # commit thiếu số bản ghi khai báo -> không lưu gì
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/commit", {}, headers=hdr)
    assert r.status_code == 422
    assert await env.scalar("SELECT count(*) FROM content_items WHERE external_id IN ('v9')") == 0


async def test_real_collector_client_push_is_accepted_by_the_real_server(env):
    """Chạy ĐÚNG mã đẩy của collector (apps/local-fallback-tool/collect.py) với server thật: hợp đồng hai phía không lệch."""
    import sys
    from pathlib import Path

    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "local-fallback-tool"))
    import collect

    viewer = env.user(perms=["capability:manual_channel_mgmt"])
    await env.projection(viewer["sub"])
    tool, job, res = await _collector_job(env, viewer)
    client = collect.IntegrationClient("http://integration.test", tool.api_key)
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, raise_app_exceptions=False), base_url="http://integration.test",
        headers={"Authorization": f"Bearer {tool.api_key}", "X-Installation-Id": tool.installation_id,
                 "X-Real-IP": "10.1.0.1"})
    try:
        assert sorted(await client.allowed_platforms()) == ["facebook", "tiktok"]
        out = await client.push_snapshot(job, [
            {"external_id": "v1", "views": 7, "likes": 1, "comments": 2, "published_at": "2026-10-04T01:00:00Z",
             "check_status": "ok"}])
        assert out["status"] == "committed"
        await client.complete_job(job, {"items": 1})   # server thật phải nhận body của collector
        assert await env.scalar("SELECT status FROM jobs WHERE id = $1", job["job_id"]) == "done"
        with pytest.raises(collect.PushError):   # bước lỗi (platform khác job) phải nổi lên, không im lặng
            await client.push_snapshot({**job, "platform": "facebook"}, [{"external_id": "v2", "views": 1}])
    finally:
        await client._http.aclose()
    row = (await env.rows("SELECT owner_account_id, platform, resource_id, data FROM content_items WHERE external_id='v1'"))[0]
    assert (row["owner_account_id"], row["platform"], row["resource_id"]) == (viewer["sub"], "tiktok", res["id"])
    assert row["data"]["metrics"] == {"views": 7, "reactions": 1, "comments": 2}


async def test_shared_tool_stores_channel_indicators_and_the_video_list_of_one_channel(env):
    """Cấu trúc dữ liệu cho Tool dùng chung: user nhập kênh -> Tool kéo danh sách video + chỉ số kênh -> lưu đúng kênh của user.

    Chỉ số kênh (followers/videos_posted/likes) vào `daily_metrics` theo NGÀY (có lịch sử); video vào `content_items` kèm
    metric + `extensions.<nền tảng>` (tiêu đề...). Gửi lại cùng ngày thì GHI ĐÈ (số tuyệt đối), không cộng dồn.
    """
    import sys
    from pathlib import Path

    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "local-fallback-tool"))
    import collect

    viewer = env.user(perms=["capability:manual_channel_mgmt"])
    await env.projection(viewer["sub"])
    tool, job, res = await _collector_job(env, viewer)
    client = collect.IntegrationClient("http://integration.test", tool.api_key)
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, raise_app_exceptions=False), base_url="http://integration.test",
        headers={"Authorization": f"Bearer {tool.api_key}", "X-Installation-Id": tool.installation_id,
                 "X-Real-IP": "10.1.0.1"})
    videos = [{"external_id": "v1", "views": 100, "likes": 10, "comments": 2, "shares": 3, "title": "Clip 1",
               "duration_seconds": 15, "published_at": "2026-10-04T01:00:00Z", "check_status": "ok"},
              {"external_id": "v2", "views": 5, "published_at": "2026-10-05T01:00:00Z", "check_status": "ok"}]
    try:
        out = await client.push_snapshot(job, videos, {"followers": 1200, "videos_posted": 2, "likes": 55})
        assert out["status"] == "committed"
        await client.push_snapshot({**job, "attempt": 2}, videos, {"followers": 1300, "videos_posted": 2})   # cùng ngày: ghi đè
    finally:
        await client._http.aclose()
    rows = await env.rows("SELECT owner_account_id, platform, resource_id, data FROM content_items ORDER BY external_id")
    assert [(r["owner_account_id"], r["platform"], r["resource_id"]) for r in rows] == [(viewer["sub"], "tiktok", res["id"])] * 2
    assert rows[0]["data"]["metrics"] == {"views": 100, "reactions": 10, "comments": 2, "shares": 3}
    assert rows[0]["data"]["extensions"] == {"tiktok": {"title": "Clip 1", "duration_seconds": 15}}
    daily = await env.rows("SELECT owner_account_id, platform, resource_id, data FROM daily_metrics")
    assert len(daily) == 1 and (daily[0]["owner_account_id"], daily[0]["platform"], daily[0]["resource_id"]) == (
        viewer["sub"], "tiktok", res["id"])
    assert daily[0]["data"]["metrics"] == {"followers": 1300, "videos_posted": 2}      # lần 2 thắng (không cộng dồn)



# ── Mã nền tảng: chặn trùng + mã phụ (alias) + Tool gửi kèm mã ────────────────────────────────────────────────────

async def _post_platform(env, u, key, *, expect=201, aliases=(), name=None):
    r = await env.http.post(ADMIN_PLATFORMS, headers=u["headers"],
                            json={"key": key, "display_name": name or key.title(), "aliases": list(aliases)})
    assert r.status_code == expect, r.text
    return r


async def test_creating_a_platform_blocks_duplicate_codes(env):
    dash, viewer = await _user(env, "dash_admin"), await _user(env)
    await _post_platform(env, viewer, "kwai", expect=403)
    r = await _post_platform(env, dash, "kwai", aliases=["kwai_vn"], name="Kwai")
    assert r.json()["data"] == {"key": "kwai", "display_name": "Kwai", "enabled": True, "description": None,
                                "aliases": ["kwai_vn"]}
    for code, owner in (("kwai", "kwai"), ("kwai_vn", "kwai"), ("facebook", "facebook")):   # chính, phụ, mã seed
        r = await _post_platform(env, dash, code, expect=409)
        assert r.json()["error"]["code"] == "PLATFORM_CODE_EXISTS"
        assert r.json()["error"]["details"] == {"code": code, "platform": owner}
    # mã phụ của nền tảng mới cũng không được trùng mã đang có
    r = await _post_platform(env, dash, "other", aliases=["kwai_vn"], expect=409)
    assert r.json()["error"]["details"]["code"] == "kwai_vn"
    assert "other" not in [p["key"] for p in (await env.http.get(ADMIN_PLATFORMS, headers=dash["headers"])).json()["data"]]
    # lặp mã ngay trong yêu cầu
    assert (await _post_platform(env, dash, "dup", aliases=["dup"], expect=409)).json()["error"]["code"] == "PLATFORM_CODE_EXISTS"
    # PUT không được biến một mã phụ thành nền tảng riêng
    r = await env.http.put(f"{ADMIN_PLATFORMS}/kwai_vn", headers=dash["headers"], json={"display_name": "X", "enabled": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PLATFORM_CODE_EXISTS"


async def test_aliases_can_be_added_and_removed_but_never_duplicated(env):
    dash = await _user(env, "dash_admin")
    base = f"{ADMIN_PLATFORMS}/tiktok/aliases"
    r = await env.http.post(base, headers=dash["headers"], json={"alias": "tt"})
    assert r.status_code == 201 and r.json()["data"]["aliases"] == ["tt"]
    assert (await env.http.post(base, headers=dash["headers"], json={"alias": "tt"})).status_code == 409
    r = await env.http.post(f"{ADMIN_PLATFORMS}/youtube/aliases", headers=dash["headers"], json={"alias": "tt"})
    assert r.status_code == 409 and r.json()["error"]["details"] == {"code": "tt", "platform": "tiktok"}
    assert (await env.http.post(base, headers=dash["headers"], json={"alias": "youtube"})).status_code == 409   # mã chính khác
    assert (await env.http.post(f"{ADMIN_PLATFORMS}/nope/aliases", headers=dash["headers"], json={"alias": "n1"})).status_code == 404
    assert (await env.http.post(base, headers=dash["headers"], json={"alias": "Bad Code"})).status_code == 422
    r = await env.http.delete(f"{base}/tt", headers=dash["headers"])
    assert r.status_code == 200 and r.json()["data"]["aliases"] == []
    assert (await env.http.delete(f"{base}/tt", headers=dash["headers"])).status_code == 404
    assert (await env.http.post(base, headers=dash["headers"], json={"alias": "tt"})).status_code == 201   # đã gỡ => dùng lại được


def _snap(platform, **over):
    return {"platform": platform, "snapshot_type": "delta", "observed_at": ts(), "report_timezone": "UTC",
            "expected_counts": {}, **over}


async def test_tool_sends_any_code_and_data_lands_on_the_one_platform(env):
    dash = await _user(env, "dash_admin")
    await env.http.post(f"{ADMIN_PLATFORMS}/tiktok/aliases", headers=dash["headers"], json={"alias": "tt"})
    tool = await env.tool(platforms=("facebook",))
    listed = {p["key"]: p for p in (await tool.get("/v1/integrations/platforms")).json()["data"]}
    assert listed["tiktok"]["codes"] == ["tiktok", "tt"] and listed["tiktok"]["aliases"] == ["tt"]
    assert listed["facebook"]["codes"] == ["facebook"]

    # resources/batch: mã phụ -> lưu theo mã chính
    r = await tool.post("/v1/integrations/resources/batch", {"items": [
        {"platform": "tt", "resource_type": "page", "external_id": "chan1", "observed_at": ts()}]}, idem=True)
    assert r.status_code == 200 and r.json()["data"]["created"] == 1, r.text
    # snapshot: mã phụ -> snapshot.platform là mã chính; content gắn đúng resource của nền tảng đó
    r = await tool.post("/v1/integrations/snapshots", _snap("tt", expected_counts={"content": 1}), idem=True)
    assert r.status_code == 201 and r.json()["data"]["platform"] == "tiktok", r.text
    sid = r.json()["data"]["snapshot_id"]
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/content", {"items": [
        {"external_id": "v1", "resource_external_id": "chan1", "published_date": "2026-10-04",
         "metrics": {"views": 5}, "observed_at": ts()}]})
    assert r.status_code == 200 and r.json()["data"]["rejected"] == 0, r.text
    assert (await tool.post(f"/v1/integrations/snapshots/{sid}/commit", {})).status_code == 200
    assert {x["platform"] for x in await env.rows("SELECT platform FROM resources")} == {"tiktok"}
    assert {x["platform"] for x in await env.rows("SELECT platform FROM content_items")} == {"tiktok"}
    await env.handshake(tool.api_key, platforms=["tt"], device_id=tool.device_id)   # khai mã phụ lúc handshake được nhận

    # mã lạ hoặc mã phụ đã gỡ / nền tảng bị tắt => từ chối, KHÔNG lưu
    r = await tool.post("/v1/integrations/snapshots", _snap("zz"), idem=True)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    await env.http.delete(f"{ADMIN_PLATFORMS}/tiktok/aliases/tt", headers=dash["headers"])
    assert (await tool.post("/v1/integrations/snapshots", _snap("tt"), idem=True)).status_code == 403
    await env.http.post(f"{ADMIN_PLATFORMS}/tiktok/aliases", headers=dash["headers"], json={"alias": "tt"})
    await _put(env, dash, "tiktok", display_name="TikTok", enabled=False)
    assert (await tool.post("/v1/integrations/snapshots", _snap("tt"), idem=True)).status_code == 403


# ── Tool nhiều nền tảng: mỗi snapshot một nền tảng; mã sai thì không lưu ở đâu cả ───────────────────────────────────────

async def _push_content(tool, platform, resource_ext, video_ext, views, *, expect_rejected=0):
    r = await tool.post("/v1/integrations/snapshots", _snap(platform, expected_counts={"content": 1 - expect_rejected}), idem=True)
    assert r.status_code == 201, r.text
    sid = r.json()["data"]["snapshot_id"]
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/content", {"items": [
        {"external_id": video_ext, "resource_external_id": resource_ext, "published_date": "2026-10-04",
         "metrics": {"views": views}, "observed_at": ts()}]})
    assert r.status_code == 200 and r.json()["data"]["rejected"] == expect_rejected, r.text
    return sid, r.json()["data"]


async def test_tool_with_data_of_several_platforms_sends_one_snapshot_per_platform(env):
    dash = await _user(env, "dash_admin")
    await env.http.post(f"{ADMIN_PLATFORMS}/tiktok/aliases", headers=dash["headers"], json={"alias": "tt"})
    tool = await env.tool(platforms=("facebook",))
    for plat, ext in (("facebook", "same-id"), ("tt", "same-id"), ("youtube", "yt-chan")):
        r = await tool.post("/v1/integrations/resources/batch", {"items": [
            {"platform": plat, "resource_type": "page", "external_id": ext, "observed_at": ts()}]}, idem=True)
        assert r.status_code == 200 and r.json()["data"]["created"] == 1, r.text
    s_fb, _ = await _push_content(tool, "facebook", "same-id", "v-fb", 10)
    s_tt, _ = await _push_content(tool, "tt", "same-id", "v-tt", 20)       # cùng external_id kênh, KHÁC nền tảng
    assert (await tool.post(f"/v1/integrations/snapshots/{s_fb}/commit", {})).status_code == 200
    assert (await tool.post(f"/v1/integrations/snapshots/{s_tt}/commit", {})).status_code == 200
    got = {(x["platform"], x["external_id"]) for x in await env.rows("SELECT platform, external_id FROM content_items")}
    assert got == {("facebook", "v-fb"), ("tiktok", "v-tt")}            # mỗi dữ liệu nằm đúng nền tảng của snapshot chứa nó
    # dữ liệu của kênh chỉ có ở nền tảng KHÁC bị từ chối, không tràn sang nền tảng của snapshot
    _, res = await _push_content(tool, "tiktok", "yt-chan", "v-x", 5, expect_rejected=1)
    assert res["rejections"][0]["code"] == "RESOURCE_UNKNOWN"


async def test_a_wrong_platform_code_stores_nothing_anywhere(env):
    tool = await env.tool(platforms=("facebook",))
    before = (await env.scalar("SELECT count(*) FROM snapshots"), await env.scalar("SELECT count(*) FROM resources"),
              await env.scalar("SELECT count(*) FROM outbox"))
    # snapshot mã sai: không tạo snapshot
    assert (await tool.post("/v1/integrations/snapshots", _snap("khong_co"), idem=True)).status_code == 403
    # resources/batch trộn mã đúng và sai: TỪ CHỐI CẢ LÔ, kể cả bản ghi mã đúng
    r = await tool.post("/v1/integrations/resources/batch", {"items": [
        {"platform": "facebook", "resource_type": "page", "external_id": "ok-1", "observed_at": ts()},
        {"platform": "khong_co", "resource_type": "page", "external_id": "bad-1", "observed_at": ts()}]}, idem=True)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"
    # thiếu platform: không nhận
    assert (await tool.post("/v1/integrations/snapshots", {k: v for k, v in _snap("facebook").items() if k != "platform"},
                            idem=True)).status_code == 422
    after = (await env.scalar("SELECT count(*) FROM snapshots"), await env.scalar("SELECT count(*) FROM resources"),
             await env.scalar("SELECT count(*) FROM outbox"))
    assert after == before      # không snapshot, không resource, không sự kiện phát ra


async def test_connection_guide_lists_the_codes_and_the_per_platform_rule(env):
    dash = await _user(env, "dash_admin")
    await env.http.post(f"{ADMIN_PLATFORMS}/tiktok/aliases", headers=dash["headers"], json={"alias": "tt"})
    owner = await env.verified_user()
    await env.tool(user=owner)
    g = (await env.http.get("/v1/integrations/me/connection-guide", headers=owner["headers"])).json()["data"]
    tiktok = next(p for p in g["platforms"] if p["key"] == "tiktok")
    assert tiktok["codes"] == ["tiktok", "tt"] and tiktok["display_name"] == "TikTok"
    assert any("MỘT nền tảng" in rule and "snapshot" in rule for rule in g["platform_rules"])
    snap_doc = next(e for e in g["endpoints"] if e["method"] == "POST" and e["path"] == "/v1/integrations/snapshots")
    allowed = snap_doc["request_schema"]["properties"]["platform"]["enum"]
    assert "tt" in allowed and "tiktok" in allowed and "khong_co" not in allowed
    batch_doc = next(e for e in g["endpoints"] if e["path"] == "/v1/integrations/resources/batch")
    item_schema = batch_doc["request_schema"]["properties"]["items"]["items"]
    assert item_schema["properties"]["platform"]["enum"] == allowed
