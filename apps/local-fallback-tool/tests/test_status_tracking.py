"""Trạng thái thu thập của từng kênh phải khoá theo `resource_id` của hệ thống (khớp kênh đã kéo trong DB cục bộ), kể cả khi
adapter dò ra id kênh khác (vd YouTube channel id)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect  # noqa: E402


class _FakeClient:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.completed: list[dict] = []

    async def fail_job(self, job, reason, *, retryable=True):
        self.failed.append(reason)

    async def complete_job(self, job, result=None):
        self.completed.append(result or {})


def _run_job(job: dict, status_file: Path, client: _FakeClient | None = None) -> dict:
    tracker = collect.StatusTracker(str(status_file))

    async def go():
        await collect.process_job(job, client or _FakeClient(), collect.Credentials(), tracker)

    asyncio.run(go())
    return json.loads(status_file.read_text(encoding="utf-8"))


def test_channel_status_is_keyed_by_the_resource_id_the_server_sends(tmp_path):
    # Hình dạng job thật của server: resource.resource_id (KHÔNG có `id`)
    job = {"job_id": "job_1", "owner_account_id": "acc_1", "platform": "instagram", "attempt": 1,
           "resource": {"resource_id": "res_42", "platform": "instagram", "external_id": "https://i/x", "display_name": "Kênh X"}}
    status = _run_job(job, tmp_path / "status.json")
    assert list(status["channels"]) == ["instagram/res_42"]
    ch = status["channels"]["instagram/res_42"]
    assert ch["channel_id"] == "res_42" and ch["channel"] == "Kênh X" and ch["owner"] == "acc_1" and ch["total_fail"] == 1
    assert status["recent_jobs"][0]["channel_id"] == "res_42"


_JOB = {"job_id": "job_e", "owner_account_id": "acc_1", "platform": "tiktok", "attempt": 1,
        "resource": {"resource_id": "res_e", "platform": "tiktok", "external_id": "https://www.tiktok.com/@empty", "display_name": "Kênh trống"}}


def test_channel_without_videos_completes_the_job_instead_of_failing_it(tmp_path, monkeypatch):
    def empty(platform, ref, creds, channel_id=None):
        raise collect.channel_collect.EmptyChannel("Kênh TikTok chưa có video")
    monkeypatch.setattr(collect.channel_collect, "collect_channel", empty)
    client = _FakeClient()
    status = _run_job(_JOB, tmp_path / "s.json", client)
    assert client.failed == [] and client.completed == [{"items": 0, "note": "Kênh TikTok chưa có video"}]   # server không thử lại job này
    ch = status["channels"]["tiktok/res_e"]
    assert ch["total_fail"] == 0 and status["recent_jobs"][0]["status"] == "done" and status["recent_jobs"][0]["items"] == 0


def test_expected_business_aborts_are_one_log_line_without_traceback(tmp_path, monkeypatch, caplog):
    def blocked(platform, ref, creds, channel_id=None):
        raise collect.JobAbort("TikTok chặn mọi lượt")
    monkeypatch.setattr(collect.channel_collect, "collect_channel", blocked)
    client = _FakeClient()
    with caplog.at_level("ERROR", logger="collect"):
        _run_job(_JOB, tmp_path / "s.json", client)
    assert client.failed == ["TikTok chặn mọi lượt"]
    assert [r.exc_info for r in caplog.records] == [None]            # không in traceback cho lỗi đã dự kiến
    assert "TikTok chặn mọi lượt" in caplog.records[0].getMessage()


def test_unexpected_errors_still_log_the_traceback(tmp_path, monkeypatch, caplog):
    def bug(platform, ref, creds, channel_id=None):
        raise RuntimeError("lỗi lập trình")
    monkeypatch.setattr(collect.channel_collect, "collect_channel", bug)
    with caplog.at_level("ERROR", logger="collect"):
        _run_job(_JOB, tmp_path / "s.json")
    assert caplog.records[0].exc_info is not None


def test_per_channel_status_survives_a_new_session_and_a_success_clears_the_old_error(tmp_path):
    f = tmp_path / "status.json"
    a = collect.StatusTracker(str(f))
    a.job_done("j1", "tiktok", "ch_a", "A", "o", 5, 10)
    a.job_failed("j2", "tiktok", "ch_b", "B", "o", "boom", 10)
    b = collect.StatusTracker(str(f))   # phiên mới (vd bấm "Chạy ngay" lần nữa) chỉ chạm kênh B
    b.job_done("j3", "tiktok", "ch_b", "B", "o", 2, 10)
    st = json.loads(f.read_text(encoding="utf-8"))
    assert st["stats"] == {"claimed": 0, "done": 1, "failed": 0} and [j["job_id"] for j in st["recent_jobs"]] == ["j3"]
    assert st["channels"]["tiktok/ch_a"]["last_ok"] and st["channels"]["tiktok/ch_a"]["last_items"] == 5
    ch_b = st["channels"]["tiktok/ch_b"]
    assert ch_b["last_error"] is None and ch_b["last_items"] == 2 and ch_b["total_fail"] == 1 and ch_b["total_ok"] == 1
