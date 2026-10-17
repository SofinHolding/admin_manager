"""Thiết kế thời điểm đẩy + ngày báo cáo của tool dùng chung:

- Đẩy NGAY sau từng link (mỗi job một snapshot), lịch chỉ quyết định khi nào collector chạy.
- Mọi ngày gửi lên tính theo giờ Việt Nam (`report_timezone` cố định); nguồn trả epoch/ISO kèm offset thì đổi thẳng, trả giờ địa phương
  không kèm offset thì dùng múi giờ của quốc gia (nếu chỉ có một).
- Mỗi kênh ĐÃ KÉO được xin job một lần mỗi ngày (giờ Việt Nam) và collector chỉ nhận job của kênh đã kéo.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channels_db  # noqa: E402
import collect  # noqa: E402
import system_api  # noqa: E402

NOW = datetime(2026, 10, 16, 18, 30, tzinfo=timezone.utc)   # = 01:30 ngày 17/10 giờ Việt Nam


# ── Ngày theo giờ Việt Nam ────────────────────────────────────────────────────────────────

def test_the_report_timezone_is_vietnam_and_fixed_by_the_shared_endpoint_file():
    assert system_api.CATALOG["report_timezone"] == "Asia/Ho_Chi_Minh"
    assert system_api.report_tz().utcoffset(NOW).total_seconds() == 7 * 3600


def test_tiktok_sample_create_time_with_region_kr_is_converted_to_the_vietnam_date():
    """Mẫu thật tikwm: create_time=1792127031 (epoch, tuyệt đối), region=KR. `region` không làm đổi mốc tuyệt đối."""
    instant = datetime.fromtimestamp(1792127031, timezone.utc)
    expected = instant.astimezone(system_api.report_tz()).date().isoformat()
    assert system_api.to_report_date(1792127031, "KR") == system_api.to_report_date(1792127031) == expected
    assert system_api.to_report_date("1792127031", "KR") == expected                     # chuỗi số
    assert system_api.to_report_date(1792127031 * 1000, "KR") == expected                # mili-giây


@pytest.mark.parametrize("value,region,expected", [
    (1792169999, None, "2026-10-16"),                    # 16:59:59Z = 23:59:59 giờ VN
    (1792170000, None, "2026-10-17"),                    # 17:00:00Z = 00:00:00 giờ VN — ranh giới ngày theo giờ VN, không phải UTC
    ("2026-10-16T17:30:00Z", None, "2026-10-17"),        # ISO có Z
    ("2026-10-17T03:00:00+09:00", "KR", "2026-10-17"),   # có offset: tuyệt đối (01:00 VN)
    ("2026-10-17T06:30:00+09:00", "KR", "2026-10-17"),
    ("2026-10-17T00:30:00+09:00", "KR", "2026-10-16"),   # 22:30 ngày 16 giờ VN
    ("2026-10-17 03:00:00", "KR", "2026-10-17"),         # giờ Hàn không offset => 01:00 VN
    ("2026-10-17 00:30:00", "KR", "2026-10-16"),         # giờ Hàn 00:30 = 22:30 ngày 16 giờ VN
    ("2026-10-17 05:00:00", None, "2026-10-17"),         # không rõ quốc gia => hiểu là UTC (12:00 VN)
    ("2026-10-16 18:00:00", "US", "2026-10-17"),         # US nhiều múi giờ => không đoán, UTC
    ("2026-10-17 03:00:00", "VN", "2026-10-17"),
    ("2026-10-17", "KR", "2026-10-17"),                  # chỉ có ngày: giữ nguyên
])
def test_conversion_cases(value, region, expected):
    assert system_api.to_report_date(value, region) == expected


@pytest.mark.parametrize("junk", [None, "", "không phải ngày", True, {}, "2026-13-45T99:99:99"])
def test_unreadable_values_fall_back_to_the_measurement_time_in_vietnam_date(junk):
    assert system_api.to_report_date(junk, fallback=NOW) == "2026-10-17"


def test_content_published_date_and_channel_metric_date_use_the_vietnam_day():
    rows = system_api.build_content_items("chan", [
        {"external_id": "v1", "views": 5, "published_at": 1792170000, "region": "kr"},     # 00:00 17/10 giờ VN
        {"external_id": "v2", "views": 6},                                                   # không có ngày đăng => ngày đo (giờ VN)
    ], NOW, "tiktok")
    by = {r["external_id"]: r for r in rows}
    assert by["v1"]["published_date"] == "2026-10-17" and by["v1"]["extensions"] == {"tiktok": {"region": "KR"}}
    assert by["v2"]["published_date"] == "2026-10-17"          # 18:30Z ngày 16 là 01:30 ngày 17 giờ VN, không phải ngày 16
    assert system_api.build_channel_metrics("chan", {"followers": 3}, NOW)[0]["date"] == "2026-10-17"
    assert all(r["observed_at"] == NOW.isoformat() for r in rows)   # mốc đo vẫn là khoảnh khắc tuyệt đối (UTC)


def test_vietnam_offset_is_used_even_when_the_machine_has_no_timezone_database(monkeypatch):
    def missing(_name):
        raise system_api.ZoneInfoNotFoundError("không có tzdata")

    monkeypatch.setattr(system_api, "ZoneInfo", missing)
    assert system_api.to_report_date(1792170000) == "2026-10-17"
    assert system_api.to_report_date(1792169999) == "2026-10-16"


def test_snapshots_declare_the_vietnam_report_timezone():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/snapshots"):
            seen.append(json.loads(req.content))
            return httpx.Response(201, json={"data": {"snapshot_id": "snp_1"}})
        return httpx.Response(200, json={"data": {"received": 1, "rejected": 0}})

    api = system_api.SystemApi("http://gw", "mpk_a.b")
    api._http = httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))
    job = {"job_id": "job_1", "platform": "tiktok", "resource": {"external_id": "x"}}
    asyncio.run(api.push_snapshot(job, [{"external_id": "v", "views": 1}]))
    assert seen[0]["report_timezone"] == "Asia/Ho_Chi_Minh"


# ── Đợt thu thập hằng ngày ────────────────────────────────────────────────────────────────

def test_each_pulled_channel_is_enqueued_once_per_vietnam_day():
    plan = collect.EnqueuePlan()
    assert plan.due(["a", "b"], "2026-10-17") == ["a", "b"]
    plan.mark(["a", "b"])
    assert plan.due(["a", "b"], "2026-10-17") == []                       # cùng ngày: không xin lại
    assert plan.due(["a", "b", "c"], "2026-10-17") == ["c"]               # kéo thêm kênh giữa phiên: chỉ kênh mới
    assert plan.due(["a", "b"], "2026-10-18") == ["a", "b"]               # sang ngày mới (collector chạy xuyên đêm): xin lại
    assert plan.due(["a"], "2026-10-18") == ["a"]


def test_a_failed_enqueue_is_retried_because_nothing_is_marked_until_it_succeeds():
    plan = collect.EnqueuePlan()
    assert plan.due(["a"], "2026-10-17") == ["a"]
    assert plan.due(["a"], "2026-10-17") == ["a"]                         # chưa mark (lần gọi server lỗi) => vẫn đến hạn


def test_enqueue_is_sent_in_batches_of_200_and_results_are_merged():
    sizes: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        ids = json.loads(req.content)["resource_ids"]
        sizes.append(len(ids))
        return httpx.Response(200, json={"data": {"created": len(ids) - 1, "already_active": 1,
                                                  "skipped": [{"resource_id": ids[0], "reason": "NOT_ELIGIBLE"}] if len(ids) > 1 else []}})

    api = system_api.SystemApi("http://gw", "mpk_a.b")
    api._http = httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))
    out = asyncio.run(api.enqueue_jobs([f"r{i}" for i in range(450)]))
    assert sizes == [200, 200, 50] and out["created"] == 447 and out["already_active"] == 3 and len(out["skipped"]) == 3


def test_claim_sends_resource_ids_only_when_there_are_some():
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"data": {"jobs": []}})

    api = system_api.SystemApi("http://gw", "mpk_a.b")
    api._http = httpx.AsyncClient(base_url="http://gw", transport=httpx.MockTransport(handler))
    api.installation_id = "ins_" + "0" * 32

    async def go():
        await api.claim_jobs(["youtube"], 5, resource_ids=["a", "b"])
        await api.claim_jobs(["youtube"], 5)

    asyncio.run(go())
    assert bodies[0]["resource_ids"] == ["a", "b"] and "resource_ids" not in bodies[1]


# ── Vòng lặp collector: chỉ làm việc với kênh đã kéo ──────────────────────────────────────────

class _Api:
    """SystemApi giả: ghi lại lời gọi; dừng vòng lặp bằng CancelledError sau `stop_after` lần claim."""
    instances: list["_Api"] = []
    stop_after = 2

    def __init__(self, *_a, **_k) -> None:
        self.enqueued: list[list[str]] = []
        self.claims: list[list[str] | None] = []
        _Api.instances.append(self)

    async def handshake(self, _platforms):
        return "ins_x"

    async def allowed_platforms(self):
        return ["youtube", "tiktok"]

    async def enqueue_jobs(self, ids):
        self.enqueued.append(list(ids))
        return {"created": len(ids), "already_active": 0, "skipped": []}

    async def claim_jobs(self, _platforms, limit=10, resource_ids=None):
        self.claims.append(list(resource_ids) if resource_ids else None)
        if len(self.claims) >= _Api.stop_after:
            raise asyncio.CancelledError
        return []

    async def aclose(self):
        pass


def _args() -> argparse.Namespace:
    return argparse.Namespace(settings_file="", gateway="http://gw", api_key="mpk_a.b", max_concurrent=2,
                              status_file="", platforms=None, poll_interval=0)


def _run(monkeypatch, pulled_by_call: list[list[str]]) -> _Api:
    _Api.instances = []
    monkeypatch.setattr(collect, "SystemApi", _Api)
    calls = iter(pulled_by_call)
    last: list[str] = []

    def fake_targets(_platforms):
        nonlocal last
        last = next(calls, last)
        return [{"id": i, "platform": "youtube"} for i in last]

    monkeypatch.setattr(collect.channels_db, "pulled_targets", fake_targets)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect.run_loop(_args()))
    return _Api.instances[0]


def test_collector_never_claims_when_no_channel_has_been_pulled(monkeypatch):
    _Api.stop_after = 99
    monkeypatch.setattr(collect, "SystemApi", _Api)
    monkeypatch.setattr(collect.channels_db, "pulled_targets", lambda _p: [])

    async def go():
        task = asyncio.create_task(collect.run_loop(_args()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _Api.instances = []
    asyncio.run(go())
    api = _Api.instances[0]
    assert api.claims == [] and api.enqueued == []        # không kênh đã kéo => không xin job, không nhận job nào


def test_collector_enqueues_pulled_channels_once_and_claims_only_them(monkeypatch):
    _Api.stop_after = 3
    api = _run(monkeypatch, [["a", "b"], ["a", "b"], ["a", "b", "c"]])
    assert api.enqueued == [["a", "b"], ["c"]]            # vòng 2 cùng ngày không xin lại; kênh c kéo thêm giữa phiên chỉ xin riêng c
    assert api.claims == [["a", "b"], ["a", "b"], ["a", "b", "c"]]     # chỉ nhận job của kênh đã kéo


def test_a_channel_pulled_during_the_session_is_enqueued_on_its_own(monkeypatch):
    _Api.stop_after = 3
    api = _run(monkeypatch, [["a"], ["a"], ["a", "c"]])
    assert api.enqueued == [["a"], ["c"]]


# ── DB: kênh nào được phép thu ─────────────────────────────────────────────────────────────

def test_pulled_targets_are_pulled_enabled_channels_of_the_requested_platforms_only(tmp_path, monkeypatch):
    monkeypatch.setattr(channels_db, "DB_PATH", str(tmp_path / "c.db"))
    conn = channels_db._get_conn()

    def ch(cid, platform):
        return {"id": cid, "owner_account_id": "u1", "owner_username": "u", "platform": platform, "display_name": cid, "input_ref": cid}

    channels_db.act_sync_shared(conn, {"users": [{"id": "u1", "username": "u"}],
                                       "channels": [ch("y1", "youtube"), ch("y2", "youtube"), ch("y3", "youtube"), ch("t1", "tiktok")]})
    channels_db.act_pull_channels(conn, {"ids": ["y1", "y2", "t1"]})
    channels_db.act_set_enabled(conn, {"ids": ["y2"], "enabled": False})
    conn.close()
    assert [t["id"] for t in channels_db.pulled_targets(["youtube"])] == ["y1"]       # y2 tắt, y3 chưa kéo, t1 khác nền tảng
    assert sorted(t["id"] for t in channels_db.pulled_targets(["youtube", "tiktok"])) == ["t1", "y1"]
    assert channels_db.pulled_targets([]) == []
