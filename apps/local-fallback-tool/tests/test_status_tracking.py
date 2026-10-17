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

    async def fail_job(self, job, reason, *, retryable=True):
        self.failed.append(reason)


def _run_job(job: dict, status_file: Path) -> dict:
    tracker = collect.StatusTracker(str(status_file))

    async def go():
        await collect.process_job(job, _FakeClient(), collect.Credentials(), tracker)

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
