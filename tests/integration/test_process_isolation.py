"""Cách ly tiến trình thật: mỗi service là một process riêng (không phải chung một process).

Mô phỏng thu nhỏ các bài kiểm tra kiến trúc bắt buộc:
  - Dừng analytics-service: identity-service và integration-service vẫn sống và sẵn sàng.
  - Khởi động lại MỘT service không buộc service khác khởi động lại (PID của service khác giữ nguyên).
  - Chưa cần Postgres/Redis: chassis không phụ thuộc hạ tầng ngoài ở bước scaffold.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = {
    "identity": ("identity-service", "identity_service"),
    "integration": ("integration-service", "integration_service"),
    "analytics": ("analytics-service", "analytics_service"),
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Proc:
    def __init__(self, key: str) -> None:
        self.key = key
        self.dir, self.package = SERVICES[key]
        self.port = _free_port()
        self.popen: subprocess.Popen | None = None

    def start(self) -> None:
        path = ROOT / "services" / self.dir
        env = {**os.environ, "PYTHONIOENCODING": "utf-8",
               "PYTHONPATH": os.pathsep.join([str(path / "src"),
                                              str(ROOT / "packages" / "observability" / "src"),
                                              str(ROOT / "packages" / "contracts" / "python")])}
        self.popen = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", f"{self.package}.main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._wait_live()

    def _wait_live(self, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.popen.poll() is not None:
                raise RuntimeError(f"{self.key} thoát sớm, mã {self.popen.returncode}")
            try:
                if httpx.get(self.url("/health/live"), timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise TimeoutError(f"{self.key} không sống sau {timeout}s")

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    @property
    def pid(self) -> int:
        return self.popen.pid

    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def stop(self) -> None:
        if self.alive():
            self.popen.terminate()
            try:
                self.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.popen.kill()

    def ready(self) -> int:
        return httpx.get(self.url("/health/ready"), timeout=2).status_code


@pytest.fixture(scope="module")
def fleet():
    procs = {k: Proc(k) for k in SERVICES}
    try:
        for p in procs.values():
            p.start()
        yield procs
    finally:
        for p in procs.values():
            p.stop()


def test_each_service_runs_in_its_own_os_process(fleet):
    pids = {p.pid for p in fleet.values()}
    assert len(pids) == 3 and os.getpid() not in pids
    for p in fleet.values():
        assert p.ready() == 200


def test_services_report_their_own_identity(fleet):
    for key, p in fleet.items():
        body = httpx.get(p.url("/health/version"), timeout=2).json()
        assert body["service"] == SERVICES[key][0]


def test_stopping_analytics_does_not_affect_identity_or_integration(fleet):
    """Bài kiểm tra A (thu nhỏ): analytics chết, login và ingest vẫn sống."""
    analytics = fleet["analytics"]
    before = {k: fleet[k].pid for k in ("identity", "integration")}
    analytics.stop()
    assert not analytics.alive()
    with pytest.raises(httpx.HTTPError):
        httpx.get(analytics.url("/health/live"), timeout=1)
    for key in ("identity", "integration"):
        assert fleet[key].alive() and fleet[key].ready() == 200
        assert httpx.get(fleet[key].url("/health/live"), timeout=2).status_code == 200
    assert {k: fleet[k].pid for k in before} == before


def test_restarting_one_service_does_not_restart_the_others(fleet):
    """Bài kiểm tra E (thu nhỏ): triển khai lại một service, PID của các service khác giữ nguyên."""
    others = {k: p.pid for k, p in fleet.items() if k != "analytics"}
    fleet["analytics"].port = _free_port()
    fleet["analytics"].start()  # khởi động lại độc lập
    assert fleet["analytics"].ready() == 200
    assert {k: fleet[k].pid for k in others} == others
    for key in others:
        assert fleet[key].alive()


def test_ready_does_not_depend_on_other_services(fleet):
    """Nếu `ready` gọi service khác, dừng một service sẽ kéo các service còn lại thành not-ready."""
    fleet["analytics"].stop()
    fleet["identity"].stop()
    try:
        assert fleet["integration"].ready() == 200
    finally:
        fleet["identity"].start()
        fleet["analytics"].port = _free_port()
        fleet["analytics"].start()
