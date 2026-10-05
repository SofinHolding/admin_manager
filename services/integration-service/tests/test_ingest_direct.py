"""Handshake, project/resource upsert ngoài snapshot, giới hạn body/batch, owner spoof, gzip."""

from __future__ import annotations

import gzip
import json

import pytest
from conftest import FEED_KEY_ANALYTICS, ts
from helpers import canonical, res

pytestmark = pytest.mark.needs_pg
HS = "/v1/integrations/handshake"
RB = "/v1/integrations/resources/batch"


def rb(ext="r1", *, obs=0, platform="facebook", **kw):
    return {"platform": platform, "resource_type": "page", "external_id": ext, "observed_at": ts(obs), **kw}


async def types(env):
    return [r["type"] for r in await env.rows("SELECT type FROM outbox ORDER BY seq")]


# ── Handshake ───────────────────────────────────────────────────────────────────

async def test_handshake_returns_owner_from_credential_and_intersects_capabilities(env):
    tool = await env.tool(platforms=["facebook", "youtube"])
    body = {"installation": {"device_id": "dev-A", "hostname": "PC-1", "os": "windows", "app_version": "2.4.0"},
            "tool": {"tool_name": "Ten Tool Tuy Y", "contract_version": "2026-10-01"},
            "platforms": ["facebook"], "capabilities": ["resources.push", "metrics.push", "jobs.pull", "browser.automation"]}
    r = await tool.post(HS, body)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["owner_account_id"] == tool.owner and d["client_id"] == tool.client_id
    assert d["accepted_contract_version"] == "2026-10-01" and d["allowed_platforms"] == ["facebook"]
    assert d["accepted_capabilities"] == ["metrics.push", "resources.push"]       # jobs.pull/ browser.* không được cấp
    assert d["installation_created"] is True and d["installation_id"].startswith("ins_")
    assert "server_time" in d and "snapshots:write" in d["allowed_scopes"]
    # tool_name chỉ là metadata: không đổi tên client, không ảnh hưởng quyền
    assert (await env.scalar("SELECT tool_name FROM integration_clients WHERE id = $1", tool.client_id)) != "Ten Tool Tuy Y"
    # Không có platforms => trả toàn bộ platform được phép
    all_p = (await tool.post(HS, {"installation": {"device_id": "dev-A"}})).json()["data"]
    assert all_p["allowed_platforms"] == ["facebook", "youtube"]


async def test_handshake_is_idempotent_per_device_and_separates_devices(env):
    tool = await env.tool()
    a1 = (await tool.post(HS, {"installation": {"device_id": "dev-A", "app_version": "1"}})).json()["data"]
    a2 = (await tool.post(HS, {"installation": {"device_id": "dev-A", "app_version": "2"}})).json()["data"]
    b = (await tool.post(HS, {"installation": {"device_id": "dev-B"}})).json()["data"]
    assert a1["installation_id"] == a2["installation_id"] and a2["installation_created"] is False
    assert b["installation_id"] != a1["installation_id"]
    row = (await env.rows("SELECT app_version, last_ip FROM installations WHERE id = $1", a1["installation_id"]))[0]
    assert row["app_version"] == "2" and row["last_ip"] == "10.1.0.1"
    assert await env.scalar("SELECT count(*) FROM installations") == 2


async def test_handshake_rejects_owner_fields_and_unknown_fields(env):
    tool = await env.tool()
    for bad in ({"installation": {"device_id": "d"}, "owner_account_id": "acc_x"},
                {"installation": {"device_id": "d", "owner_account_id": "acc_x"}},
                {"installation": {"device_id": "d"}, "bogus": 1}, {"installation": {}}, {}):
        r = await tool.post(HS, bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED", bad
    assert await env.scalar("SELECT count(*) FROM installations") == 0


# ── Project ─────────────────────────────────────────────────────────────────────

async def test_project_upsert_create_update_and_noop(env):
    tool = await env.tool()
    body = {"name": "ITL", "platform": "facebook", "country_code": "BR", "metadata": {"campaign": "x"}}
    c = (await tool.put("/v1/integrations/projects/itl", body)).json()["data"]
    assert c["created"] is True and c["changed"] is True and c["project"]["revision"] == 1
    same = (await tool.put("/v1/integrations/projects/itl", body)).json()["data"]
    assert same["created"] is False and same["changed"] is False and same["project"]["revision"] == 1
    upd = (await tool.put("/v1/integrations/projects/itl", {**body, "name": "ITL 2"})).json()["data"]
    assert upd["changed"] is True and upd["project"]["revision"] == 2 and upd["project"]["name"] == "ITL 2"
    items = (await canonical(env, "projects"))["items"]
    assert len(items) == 1 and items[0]["project_key"] == "itl" and items[0]["owner_account_id"] == tool.owner


async def test_project_validation_and_owner_spoof(env):
    tool = await env.tool()
    for key, body in (("itl", {"name": "n", "owner_account_id": "acc_x"}), ("itl", {}), ("itl", {"name": ""}),
                      ("itl", {"name": "n", "country_code": "br"}), ("itl", {"name": "n", "bogus": 1}),
                      ("BAD-KEY", {"name": "n"}), ("1abc", {"name": "n"})):
        r = await tool.put(f"/v1/integrations/projects/{key}", body)
        assert r.status_code == 422, (key, body)
        assert r.json()["error"]["code"] == "PROJECT_VALIDATION_FAILED"
    assert await env.scalar("SELECT count(*) FROM projects") == 0


async def test_projects_are_scoped_per_owner(env):
    a, b = await env.tool(), await env.tool()
    await a.put("/v1/integrations/projects/itl", {"name": "A"})
    await b.put("/v1/integrations/projects/itl", {"name": "B"})
    rows = await env.rows("SELECT owner_account_id, name FROM projects ORDER BY name")
    assert [(r["name"]) for r in rows] == ["A", "B"] and rows[0]["owner_account_id"] != rows[1]["owner_account_id"]


# ── Resource batch ──────────────────────────────────────────────────────────────

async def test_resource_batch_upsert_lifecycle_with_events_and_revisions(env):
    tool = await env.tool()
    r = await tool.post(RB, {"items": [rb("r1", display_name="One", project_key="itl", country_code="BR"),
                                       rb("r2", display_name="Two")]})
    d = r.json()["data"]
    assert (d["received"], d["created"], d["updated"], d["unchanged"], d["rejected"]) == (2, 2, 0, 0, 0)
    assert [i["status"] for i in d["items"]] == ["created", "created"] and d["items"][0]["resource_id"].startswith("res_")
    assert (await types(env)).count("resource.changed") == 2
    head1 = (await env.http.get("/internal/v1/canonical/summary", headers={"X-Service-Key": FEED_KEY_ANALYTICS})).json()["head_revision"]

    same = (await tool.post(RB, {"items": [rb("r1", display_name="One", project_key="itl", country_code="BR")]})).json()["data"]
    assert same["unchanged"] == 1 and same["created"] == 0 and (await types(env)).count("resource.changed") == 2
    head2 = (await env.http.get("/internal/v1/canonical/summary", headers={"X-Service-Key": FEED_KEY_ANALYTICS})).json()["head_revision"]
    assert head2 == head1                                   # gửi lại y hệt: KHÔNG tăng revision

    newer = (await tool.post(RB, {"items": [rb("r1", obs=5, display_name="One v2", project_key="itl", country_code="BR")]})).json()["data"]
    assert newer["updated"] == 1 and newer["items"][0]["status"] == "updated"
    older = (await tool.post(RB, {"items": [rb("r1", obs=-5, display_name="CU HON")]})).json()["data"]
    assert older["stale_ignored"] == 1 and older["items"][0]["status"] == "stale_ignored"
    cur = {i["external_id"]: i for i in (await canonical(env, "resources"))["items"]}
    assert cur["r1"]["display_name"] == "One v2" and cur["r1"]["country"] == {"code": "BR", "name": None}
    assert cur["r1"]["project_keys"] == ["itl"]
    assert (await types(env)).count("resource.changed") == 3


async def test_resource_batch_partial_rejections_are_reported_not_dropped(env):
    tool = await env.tool()
    items = [rb("ok1"), {"platform": "facebook", "resource_type": "Page", "external_id": "bad1", "observed_at": ts()},
             rb("ok2"), {"platform": "facebook", "resource_type": "page", "observed_at": ts()},
             "khong-phai-object", rb("bad2", observed_at="05/10/2026")]
    items[-1]["observed_at"] = "05/10/2026"
    d = (await tool.post(RB, {"items": items})).json()["data"]
    assert d["received"] == 6 and d["accepted"] == 2 and d["created"] == 2 and d["rejected"] == 4
    assert [x["index"] for x in d["rejections"]] == [1, 3, 4, 5]
    assert all(x["code"] == "RECORD_VALIDATION_FAILED" for x in d["rejections"])
    assert d["rejections"][0]["external_id"] == "bad1"
    assert await env.scalar("SELECT count(*) FROM resources") == 2


async def test_owner_fields_in_payload_reject_the_whole_request(env):
    tool = await env.tool()
    for payload in ({"items": [rb("a"), rb("b", owner_account_id="acc_gia_mao")]},
                    {"items": [rb("a")], "owner_account_id": "acc_gia_mao"},
                    {"items": [rb("a", owner="acc_gia_mao")]}):
        r = await tool.post(RB, payload)
        assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED"
        assert "acc_gia_mao" not in r.text
    assert await env.scalar("SELECT count(*) FROM resources") == 0
    # đường snapshot cũng vậy
    sid = (await tool.post("/v1/integrations/snapshots", {"platform": "facebook", "snapshot_type": "delta",
                                                          "observed_at": ts(), "report_timezone": "UTC",
                                                          "expected_counts": {}})).json()["data"]["snapshot_id"]
    r = await tool.post(f"/v1/integrations/snapshots/{sid}/resources",
                        {"items": [res("a", owner_account_id="acc_gia_mao")]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED"
    r = await tool.post("/v1/integrations/snapshots", {"platform": "facebook", "snapshot_type": "delta",
                                                       "observed_at": ts(), "report_timezone": "UTC",
                                                       "expected_counts": {}, "owner_account_id": "acc_gia_mao"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED"
    assert "owner" in r.json()["error"]["message"]


async def test_ownership_comes_from_credential_two_owners_never_collide(env):
    a, b = await env.tool(), await env.tool()
    await a.post(RB, {"items": [rb("same", display_name="cua A")]})
    await b.post(RB, {"items": [rb("same", display_name="cua B")]})
    rows = await env.rows("SELECT owner_account_id, data->>'display_name' AS n FROM resources ORDER BY n")
    assert {(r["owner_account_id"], r["n"]) for r in rows} == {(a.owner, "cua A"), (b.owner, "cua B")}


async def test_same_external_id_on_two_platforms_are_two_resources(env):
    tool = await env.tool(platforms=["facebook", "youtube"])
    await tool.post(RB, {"items": [rb("x", platform="facebook"), rb("x", platform="youtube")]})
    assert await env.scalar("SELECT count(*) FROM resources") == 2


async def test_batch_size_limits(env):
    tool = await env.tool()
    ok = await tool.post(RB, {"items": [rb(f"r{i}") for i in range(500)]})
    assert ok.status_code == 200 and ok.json()["data"]["created"] == 500
    big = await tool.post(RB, {"items": [rb(f"s{i}") for i in range(501)]})
    assert big.status_code == 413 and big.json()["error"]["code"] == "BATCH_TOO_LARGE"
    assert big.json()["error"]["details"] == {"max": 500, "received": 501}
    assert await env.scalar("SELECT count(*) FROM resources") == 500
    for bad in ({"items": []}, {"items": "x"}, {}, {"items": [rb("a")], "extra": 1}, [1, 2]):
        r = await tool.post(RB, bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED", bad
    r = await tool.post(RB, content=b"{khong-phai-json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "RECORD_VALIDATION_FAILED"
    r = await tool.post(RB, content=b'{"items": [{"a": NaN}]}')
    assert r.status_code == 422


async def test_body_size_limit_returns_413_before_processing(make_env):
    env, _ = await make_env(max_body_bytes=2048)
    tool = await env.tool()
    payload = {"items": [rb(f"r{i}", display_name="x" * 100) for i in range(40)]}
    r = await tool.post(RB, payload)
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE" and r.json()["ok"] is False
    assert "x-request-id" in r.headers
    assert await env.scalar("SELECT count(*) FROM resources") == 0
    # thiếu Content-Length (chunked) cũng bị chặn theo số byte thực nhận
    async def gen():
        yield b'{"items": ['
        for _ in range(100):
            yield b'{"filler": "' + b"y" * 100 + b'"},'
    r = await env.http.post(RB, content=gen(), headers=tool.h())
    assert r.status_code == 413


async def test_gzip_body_is_supported_and_decompression_bomb_is_capped(make_env):
    env, _ = await make_env(max_body_bytes=4096)
    tool = await env.tool()
    body = json.dumps({"items": [rb(f"g{i}") for i in range(5)]}).encode()
    r = await env.http.post(RB, content=gzip.compress(body), headers=tool.h(**{"Content-Encoding": "gzip",
                                                                              "Content-Type": "application/json"}))
    assert r.status_code == 200 and r.json()["data"]["created"] == 5
    bomb = gzip.compress(b'{"items": ["' + b"0" * 3_000_000 + b'"]}')
    assert len(bomb) < 4096                       # nhỏ khi nén, khổng lồ khi bung
    r = await env.http.post(RB, content=bomb, headers=tool.h(**{"Content-Encoding": "gzip"}))
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    r = await env.http.post(RB, content=b"khong phai gzip", headers=tool.h(**{"Content-Encoding": "gzip"}))
    assert r.status_code == 400
    r = await env.http.post(RB, content=b"{}", headers=tool.h(**{"Content-Encoding": "br"}))
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_ENCODING"
    assert await env.scalar("SELECT count(*) FROM resources") == 5


async def test_nul_character_is_rejected_per_record_not_500(env):
    tool = await env.tool()
    d = (await tool.post(RB, {"items": [rb("ok"), rb("bad\u0000x")]})).json()["data"]
    assert d["created"] == 1 and d["rejected"] == 1
    r = await tool.post(RB, content=b'{"items": [{"platform": "facebook", "resource_type": "page", "external_id": "a\\u0000b"}]}')
    assert r.status_code == 200 and r.json()["data"]["rejected"] == 1


async def test_admin_can_toggle_resource_and_it_emits_change(env):
    tool = await env.tool()
    rid = (await tool.post(RB, {"items": [rb("r1")]})).json()["data"]["items"][0]["resource_id"]
    admin = await env.verified_user(role="admin")
    viewer = await env.verified_user()
    path = f"/v1/integrations/admin/resources/{rid}"
    assert (await env.http.patch(path, headers=viewer["headers"], json={"state": "disabled"})).status_code == 403
    r = await env.http.patch(path, headers=admin["headers"], json={"state": "disabled"})
    assert r.status_code == 200 and r.json()["data"]["state"] == "disabled"
    ev = [e for e in await env.rows("SELECT data FROM outbox WHERE type = 'resource.changed' ORDER BY seq")][-1]["data"]
    assert ev["resource_id"] == rid and ev["state"] == "disabled" and ev["owner_account_id"] == tool.owner
    assert (await canonical(env, "resources"))["items"][0]["state"] == "disabled"
    assert (await env.http.patch("/v1/integrations/admin/resources/res_khongco", headers=admin["headers"],
                                 json={"state": "active"})).status_code == 404


async def test_registry_is_data_new_platform_and_metric_without_code_change(env):
    admin = await env.verified_user(role="admin")
    viewer = await env.verified_user()
    assert (await env.http.put("/v1/integrations/admin/platforms/threads", headers=viewer["headers"],
                               json={"display_name": "Threads"})).status_code == 403
    r = await env.http.put("/v1/integrations/admin/platforms/threads", headers=admin["headers"],
                           json={"display_name": "Threads"})
    assert r.status_code == 200
    assert (await env.http.put("/v1/integrations/admin/metrics/saves", headers=admin["headers"],
                               json={"display_name": "Saves"})).status_code == 200
    assert (await env.http.put("/v1/integrations/admin/platforms/Bad-Key", headers=admin["headers"], json={})).status_code == 422
    tool = await env.tool(platforms=["threads"])
    sid = (await tool.post("/v1/integrations/snapshots", {"platform": "threads", "snapshot_type": "delta",
                                                          "observed_at": ts(), "report_timezone": "UTC",
                                                          "expected_counts": {}})).json()["data"]["snapshot_id"]
    await tool.post(f"/v1/integrations/snapshots/{sid}/resources", {"items": [res("t1")]})
    d = (await tool.post(f"/v1/integrations/snapshots/{sid}/content", {"items": [
        {"external_id": "p", "resource_external_id": "t1", "published_date": "2026-10-04", "metrics": {"saves": 3},
         "observed_at": ts()}]})).json()["data"]
    assert d["created"] == 1 and d["rejected"] == 0
    listed = (await env.http.get("/v1/integrations/admin/metrics", headers=admin["headers"])).json()["data"]
    assert "saves" in {m["key"] for m in listed}
