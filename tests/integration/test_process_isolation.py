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


PG_HOST = os.environ.get("TEST_PG_HOST")
DEAD_DSN = "postgresql://identity_svc:x@127.0.0.1:1/identity_db"  # cổng 1: không ai nghe


def identity_dsn(alive: bool) -> str:
    if alive and PG_HOST:
        port = os.environ.get("TEST_PG_PORT", "5432")
        return f"postgresql://identity_svc:dev_identity_change_me@{PG_HOST}:{port}/identity_db"
    return DEAD_DSN


class Proc:
    def __init__(self, key: str, *, db_alive: bool = True) -> None:
        self.key = key
        self.db_alive = db_alive
        self.dir, self.package = SERVICES[key]
        self.port = _free_port()
        self.popen: subprocess.Popen | None = None

    def start(self) -> None:
        path = ROOT / "services" / self.dir
        extra = {}
        if self.key == "identity":  # cấu hình bắt buộc của identity-service (thiếu thì nó từ chối khởi động)
            extra = {"IDENTITY_DATABASE_URL": identity_dsn(self.db_alive), "IDENTITY_ENV": "test",
                     "IDENTITY_JWT_COMPAT_SECRET": "isolation-test-secret-16+"}
        env = {**os.environ, **extra, "PYTHONIOENCODING": "utf-8",
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

    @property
    def expected_ready(self) -> int:
        """identity cần DB thật đã migrate; không có Postgres thì 'chưa sẵn sàng' (503) là đúng, không phải lỗi."""
        if self.key == "identity" and not (PG_HOST and self.db_alive):
            return 503
        return 200


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
        assert p.ready() == p.expected_ready


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
        assert fleet[key].alive() and fleet[key].ready() == fleet[key].expected_ready
        assert httpx.get(fleet[key].url("/health/live"), timeout=2).status_code == 200
    assert {k: fleet[k].pid for k in before} == before


def test_restarting_one_service_does_not_restart_the_others(fleet):
    """Bài kiểm tra E (thu nhỏ): triển khai lại một service, PID của các service khác giữ nguyên."""
    others = {k: p.pid for k, p in fleet.items() if k != "analytics"}
    fleet["analytics"].port = _free_port()
    fleet["analytics"].start()  # khởi động lại độc lập
    assert fleet["analytics"].ready() == fleet["analytics"].expected_ready
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


def test_identity_with_dead_database_stays_alive_and_isolated(fleet):
    """Fault isolation: DB của identity chết -> identity báo not-ready/503 nhưng KHÔNG crash; service khác vẫn ổn."""
    dead = Proc("identity", db_alive=False)
    try:
        dead.start()
        assert httpx.get(dead.url("/health/live"), timeout=2).status_code == 200
        assert dead.ready() == 503
        started = time.time()
        r = httpx.post(dead.url("/v1/auth/check-username"), json={"username": "abc"}, timeout=20)
        assert r.status_code == 503 and r.json() == {"detail": "Dịch vụ tạm thời không khả dụng"}
        assert time.time() - started < 12, "DB chết phải thất bại NHANH, không treo cho tới timeout mặc định 60s"
        assert dead.alive()  # request lỗi DB không làm sập tiến trình
        assert fleet["integration"].ready() == fleet["integration"].expected_ready
    finally:
        dead.stop()


def test_identity_refuses_to_start_without_required_config():
    """Cấu hình thiếu là lỗi khởi động (fail-fast), khác với dependency tạm chết."""
    path = ROOT / "services" / "identity-service"
    env = {k: v for k, v in os.environ.items() if not k.startswith("IDENTITY_")}
    env["PYTHONPATH"] = os.pathsep.join([str(path / "src"), str(ROOT / "packages" / "observability" / "src"),
                                         str(ROOT / "packages" / "contracts" / "python")])
    proc = subprocess.run([sys.executable, "-c", "import identity_service.main"], cwd=path, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert proc.returncode != 0 and "IDENTITY_DATABASE_URL" in proc.stderr


def test_every_log_line_of_a_running_service_is_json():
    """Chassis cam kết log JSON có cấu trúc. Kiểm bằng tiến trình thật (uvicorn cài handler riêng nên dễ lọt)."""
    import json

    path = ROOT / "services" / "identity-service"
    port = _free_port()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "IDENTITY_DATABASE_URL": DEAD_DSN, "IDENTITY_ENV": "test",
           "IDENTITY_JWT_COMPAT_SECRET": "isolation-test-secret-16+",
           "PYTHONPATH": os.pathsep.join([str(path / "src"), str(ROOT / "packages" / "observability" / "src"),
                                          str(ROOT / "packages" / "contracts" / "python")])}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "identity_service.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=path, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        httpx.get(f"http://127.0.0.1:{port}/v1/khong-co", timeout=2)
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=15)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "tiến trình không ghi log nào"
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except ValueError:
            pytest.fail(f"dòng log không phải JSON: {ln!r}")
    assert all({"ts", "level", "service_name", "message"} <= set(rec) for rec in parsed)
    assert {rec["service_name"] for rec in parsed} == {"identity-service"}
    assert any("404" in rec["message"] for rec in parsed)  # log truy cập cũng là JSON
