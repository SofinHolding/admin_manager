"""Test đường đẩy số liệu của collector (mapping kênh/nền tảng, kiểm lỗi, file kết nối). Không cần mạng/DB."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect  # noqa: E402

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
JOB = {"job_id": "job_1", "platform": "youtube", "attempt": 2,
       "resource": {"resource_id": "res_1", "platform": "youtube", "external_id": "chan-user-entered"}}


def test_content_is_keyed_to_the_jobs_resource_not_to_what_the_tool_discovered():
    rows = collect.build_content_items("chan-user-entered", [
        {"external_id": "vid1", "views": 10, "likes": 2, "comments": 1, "published_at": "2026-10-01T08:00:00Z",
         "url": "https://y/1"}], NOW)
    assert rows == [{"external_id": "vid1", "resource_external_id": "chan-user-entered", "published_date": "2026-10-01",
                     "metrics": {"views": 10, "reactions": 2, "comments": 1}, "observed_at": NOW.isoformat(),
                     "url": "https://y/1"}]


def test_unmeasured_negative_and_idless_values_are_not_invented():
    rows = collect.build_content_items("r", [
        {"external_id": "", "views": 5},
        {"external_id": "a", "views": -1, "likes": None, "comments": 3},
        {"external_id": "a", "views": 7},
    ], NOW)
    assert [r["metrics"] for r in rows] == [{"views": 7}]          # bản sau thắng; số âm/None không thành 0


def test_long_external_id_is_hashed_to_fit_contract():
    rows = collect.build_content_items("r", [{"external_id": "https://x/" + "a" * 300, "views": 1}], NOW)
    assert len(rows[0]["external_id"]) <= 200 and rows[0]["external_id"].startswith("h_")


def test_video_details_go_to_the_platform_extension_and_shares_to_metrics():
    rows = collect.build_content_items("r", [{"external_id": "v", "views": 1, "shares": 4, "title": "T", "duration_seconds": 9,
                                              "cover_url": ""}], NOW, "tiktok")
    assert rows[0]["metrics"] == {"views": 1, "shares": 4}
    assert rows[0]["extensions"] == {"tiktok": {"title": "T", "duration_seconds": 9}}      # rỗng/thiếu thì không gửi


def test_channel_metrics_are_one_row_per_day_and_never_invented():
    rows = collect.build_channel_metrics("chan", {"followers": 12, "videos_posted": 0, "likes": -1, "junk": 5}, NOW)
    assert rows == [{"resource_external_id": "chan", "date": "2026-10-15", "metrics": {"followers": 12, "videos_posted": 0},
                     "observed_at": NOW.isoformat()}]
    assert collect.build_channel_metrics("chan", {}, NOW) == [] and collect.build_channel_metrics("chan", None, NOW) == []


def test_platforms_are_allowed_keys_that_have_an_adapter():
    assert collect.select_platforms(["facebook", "instagram", "youtube"], None) == ["youtube", "facebook"]
    assert collect.select_platforms(["facebook", "youtube"], ["facebook"]) == ["facebook"]
    assert collect.select_platforms(["instagram"], None) == []


def _write(tmp_path, payload) -> str:
    p = tmp_path / "conn.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return str(p)


def test_connection_file_validation(tmp_path):
    ok = {"format": "manager-connection/1", "gateway": "https://dash.example/", "api_key": "mpk_abc.def"}
    assert collect.load_connection_file(_write(tmp_path, ok)) == {"gateway": "https://dash.example", "api_key": "mpk_abc.def"}
    for bad in ({**ok, "format": "x"}, {**ok, "gateway": "ftp://h"}, {**ok, "api_key": "secret"}, "not json"):
        with pytest.raises(collect.ConnectionFileError):
            collect.load_connection_file(_write(tmp_path, bad))
    with pytest.raises(collect.ConnectionFileError):
        collect.load_connection_file(str(tmp_path / "missing.json"))


def _client(handler) -> collect.IntegrationClient:
    c = collect.IntegrationClient("http://gw", "mpk_a.b")
    c._http = httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))
    return c


def test_push_sends_delta_snapshot_with_job_platform_and_commits():
    seen: list[tuple[str, str, dict, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        seen.append((req.method, req.url.path, body, dict(req.headers)))
        if req.url.path.endswith("/snapshots"):
            return httpx.Response(201, json={"data": {"snapshot_id": "snp_1"}})
        if req.url.path.endswith("/content"):
            return httpx.Response(200, json={"data": {"received": 1, "rejected": 0, "rejections": []}})
        return httpx.Response(200, json={"data": {"status": "committed"}})

    out = asyncio.run(_client(handler).push_snapshot(JOB, [{"external_id": "v1", "views": 3}]))
    assert out == {"status": "committed"}
    create, content, commit = seen
    assert create[2]["platform"] == "youtube" and create[2]["snapshot_type"] == "delta"
    assert create[2]["expected_counts"] == {"content": 1}
    assert create[3]["idempotency-key"] == "collect-job_1-2"
    assert content[2]["items"][0]["resource_external_id"] == "chan-user-entered"
    assert commit[1] == "/v1/integrations/snapshots/snp_1/commit"
    assert all(s[3]["x-delegated-job"] == "job_1" for s in seen)


@pytest.mark.parametrize("fail_at", ["/snapshots", "/content", "/commit"])
def test_any_failed_step_raises_instead_of_reporting_success(fail_at):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith(fail_at):
            return httpx.Response(422, json={"error": {"code": "SNAPSHOT_INCOMPLETE"}})
        if req.url.path.endswith("/snapshots"):
            return httpx.Response(201, json={"data": {"snapshot_id": "snp_1"}})
        return httpx.Response(200, json={"data": {"received": 1, "rejected": 0}})

    with pytest.raises(collect.PushError):
        asyncio.run(_client(handler).push_snapshot(JOB, [{"external_id": "v1", "views": 3}]))


def test_server_rejections_are_not_silent():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/snapshots"):
            return httpx.Response(201, json={"data": {"snapshot_id": "snp_1"}})
        return httpx.Response(200, json={"data": {"received": 1, "rejected": 1, "rejections": [{"code": "RESOURCE_UNKNOWN"}]}})

    with pytest.raises(collect.PushError, match="từ chối"):
        asyncio.run(_client(handler).push_snapshot(JOB, [{"external_id": "v1", "views": 3}]))


def test_job_without_resource_cannot_be_mapped():
    with pytest.raises(collect.PushError):
        asyncio.run(_client(lambda r: httpx.Response(500)).push_snapshot(
            {"job_id": "j", "platform": "youtube", "resource": None}, [{"external_id": "v", "views": 1}]))


def test_job_transitions_use_the_servers_schema():
    """`complete`/`fail`/`heartbeat` bị server từ chối (422) nếu thiếu body / dùng trường lạ — từng bị nuốt im lặng."""
    seen: dict[str, dict] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen[req.url.path.rsplit("/", 1)[-1]] = json.loads(req.content)
        return httpx.Response(200, json={"data": {}})

    job = {**JOB, "lease_token": "tok"}
    c = _client(handler)

    async def go():
        await c.complete_job(job, {"items": 2})
        await c.fail_job(job, "boom", retryable=False)
        await c.heartbeat(job)

    asyncio.run(go())
    assert seen["complete"] == {"result": {"items": 2}, "lease_token": "tok"}
    assert seen["fail"] == {"error_code": "COLLECT_FAILED", "message": "boom", "retryable": False, "lease_token": "tok"}
    assert seen["heartbeat"] == {"lease_seconds": 300, "lease_token": "tok"}
