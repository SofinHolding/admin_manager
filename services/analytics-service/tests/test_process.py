"""Chạy analytics-service như TIẾN TRÌNH THẬT (uvicorn + migration job) nối với Integration/Identity giả cũng là tiến
trình HTTP thật: fail-fast cấu hình, đồng bộ nền, nguồn chết, DB chết, Redis chết, log không lộ bí mật."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

import conftest as cf
from analytics_testsupport import (ADMIN, ISS, OWN_A, FakeIdentityFeed, FakeIntegration, Issuer, post, res,
                                   seed_world)

pytestmark = pytest.mark.needs_pg

SERVICE_DIR = Path(__file__).resolve().parents[1]
ROOT = SERVICE_DIR.parents[1]
PYPATH = os.pathsep.join(str(p) for p in (SERVICE_DIR / "src", ROOT / "packages" / "observability" / "src",
                                           ROOT / "packages" / "contracts" / "python", ROOT / "packages" / "sdk-python"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerThread:
    def __init__(self, app) -> None:
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "ServerThread":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        assert self.server.started
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def base_env(**over) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANALYTICS_")}
    env.update(PYTHONPATH=PYPATH, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    env.update(over)
    return env


@pytest.fixture
def stack(keys, tmp_path):
    """Integration + Identity giả (HTTP thật) và tiến trình analytics thật. Trả về đối tượng điều khiển."""
    issuer = Issuer()
    integration = FakeIntegration(keys["integration"])
    identity = FakeIdentityFeed(keys["identity"])

    @identity.app.get("/.well-known/jwks.json")
    async def jwks():
        return issuer.jwks()

    srv_int, srv_id = ServerThread(integration.app).start(), ServerThread(identity.app).start()
    procs: list[subprocess.Popen] = []
    log = tmp_path / "analytics.log"

    class Stack:
        pass

    s = Stack()
    s.issuer, s.integration, s.identity, s.srv_int, s.srv_id, s.log = issuer, integration, identity, srv_int, srv_id, log

    def env(**over) -> dict:
        e = dict(ANALYTICS_DATABASE_URL=cf.dsn(), ANALYTICS_ISSUER=ISS, ANALYTICS_AUDIENCE="manager-api",
                 ANALYTICS_IDENTITY_JWKS_URL=srv_id.url + "/.well-known/jwks.json",
                 ANALYTICS_INTEGRATION_URL=srv_int.url, ANALYTICS_INTEGRATION_FEED_KEY=keys["integration"],
                 ANALYTICS_IDENTITY_URL=srv_id.url, ANALYTICS_IDENTITY_FEED_KEY=keys["identity"],
                 ANALYTICS_SYNC_INTERVAL_S="0.2", ANALYTICS_CONSUMER_BACKOFF_BASE_S="0.0", ANALYTICS_LOG_LEVEL="INFO")
        e.update(over)
        return base_env(**e)

    def migrate(**over) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "analytics_service.migrate"], cwd=SERVICE_DIR, env=env(**over),
                              capture_output=True, encoding="utf-8", errors="replace", timeout=60)

    def start(**over) -> str:
        port = free_port()
        handle = log.open("ab")
        p = subprocess.Popen([sys.executable, "-m", "uvicorn", "analytics_service.main:app", "--host", "127.0.0.1",
                              "--port", str(port)], cwd=SERVICE_DIR, env=env(ANALYTICS_PORT=str(port), **over),
                             stdout=handle, stderr=subprocess.STDOUT)
        procs.append(p)
        url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(url + "/health/live", timeout=1).status_code == 200:
                    return url
            except httpx.HTTPError:
                time.sleep(0.2)
        raise AssertionError("tiến trình analytics không lên: " + log.read_text(encoding="utf-8", errors="replace")[-2000:])

    s.env, s.migrate, s.start = env, migrate, start
    yield s
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    for srv in (srv_int, srv_id):
        if srv.thread.is_alive():
            srv.stop()


def wait_for(fn, timeout=15.0, interval=0.2):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"hết thời gian chờ; giá trị cuối: {last!r}")


def test_missing_required_config_fails_fast_without_leaking(keys):
    out = subprocess.run([sys.executable, "-c", "import analytics_service.main"], cwd=SERVICE_DIR,
                         env=base_env(ANALYTICS_INTEGRATION_FEED_KEY=keys["integration"]), capture_output=True,
                         encoding="utf-8", errors="replace", timeout=30)
    assert out.returncode != 0 and "ANALYTICS_DATABASE_URL" in out.stderr
    assert keys["integration"] not in out.stderr + out.stdout


def test_migrate_job_is_separate_idempotent_and_fails_clearly(stack):
    first = stack.migrate()
    assert first.returncode == 0, first.stderr
    again = stack.migrate()
    assert again.returncode == 0 and "không có gì mới" in again.stdout
    bad = stack.migrate(ANALYTICS_DATABASE_URL="postgresql://nobody:x@127.0.0.1:1/none")
    assert bad.returncode == 1 and "Migration lỗi" in bad.stderr
    assert stack.migrate(ANALYTICS_DATABASE_URL="").returncode == 2


def test_real_process_syncs_serves_and_survives_dead_sources(stack, keys, db_clean):
    seed_world(stack.integration)
    url = stack.start()
    h = stack.issuer.headers(OWN_A)

    def total():
        r = httpx.get(url + "/v1/analytics/summary", params={"date_from": "2026-10-01", "date_to": "2026-10-03"},
                      headers=h, timeout=5)
        return r.json().get("total_views") if r.status_code == 200 else None

    assert wait_for(lambda: total() == 560), "consumer nền phải tự đồng bộ"
    assert httpx.get(url + "/health/ready", timeout=3).status_code == 200

    # snapshot mới -> tự đuổi kịp, số của ngày đăng cũ đổi
    stack.integration.update_item("content", owner_account_id=OWN_A, platform="facebook", resource_external_id="pg1",
                                  external_id="p1", metrics={"views": 400, "reactions": 10, "comments": 1})
    assert wait_for(lambda: total() == 860)

    # nguồn Integration CHẾT (tiến trình dừng thật): vẫn trả dữ liệu cũ, kèm stale
    stack.srv_int.stop()

    adm_h = stack.issuer.headers(ADMIN, "admin")

    def source_down():
        src = httpx.get(url + "/v1/analytics/freshness", headers=adm_h, timeout=5).json()["sources"]
        return (src.get("integration") or {}).get("ok") is False

    assert wait_for(source_down, timeout=20)
    r = httpx.get(url + "/v1/analytics/summary", params={"date_from": "2026-10-01", "date_to": "2026-10-03"},
                  headers=h, timeout=5).json()
    assert r["freshness"]["stale"] is True and r["total_views"] == 860          # dữ liệu cũ vẫn đọc được
    assert httpx.get(url + "/health/ready", timeout=3).status_code == 200

    # nhật ký không chứa token / service key / secret
    text = stack.log.read_text(encoding="utf-8", errors="replace")
    assert keys["integration"] not in text and keys["identity"] not in text
    assert h["Authorization"].split(" ")[1] not in text


def test_database_dead_process_lives_ready_503_api_503_fast(stack):
    url = stack.start(ANALYTICS_DATABASE_URL="postgresql://nobody:x@127.0.0.1:1/none")
    assert httpx.get(url + "/health/live", timeout=3).status_code == 200
    t0 = time.time()
    r = httpx.get(url + "/health/ready", timeout=5)
    assert r.status_code == 503 and r.json()["checks"] == {"database": False}
    api = httpx.get(url + "/v1/analytics/summary", headers=stack.issuer.headers(OWN_A), timeout=10)
    assert api.status_code == 503 and api.json()["error"]["code"] == "UNAVAILABLE"
    assert time.time() - t0 < 8
    legacy = httpx.get(url + "/v1/viewer/summary", headers=stack.issuer.headers(OWN_A), timeout=10)
    assert legacy.status_code == 503 and set(legacy.json()) == {"detail"}
    assert httpx.get(url + "/health/live", timeout=3).status_code == 200          # vẫn sống sau các lỗi trên


def test_dead_redis_does_not_break_the_process(stack, db_clean):
    stack.integration.commit(owner=OWN_A, platform="facebook",
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("p1", "pg1", "2026-10-01", 77)])
    url = stack.start(ANALYTICS_REDIS_URL="redis://127.0.0.1:1/0")
    h = stack.issuer.headers(OWN_A)

    def ok():
        r = httpx.get(url + "/v1/analytics/summary", params={"date_from": "2026-10-01", "date_to": "2026-10-03"},
                      headers=h, timeout=5)
        return r.status_code == 200 and r.json()["total_views"] == 77

    assert wait_for(ok)
    assert httpx.get(url + "/health/ready", timeout=3).status_code == 200


def test_wrong_feed_key_marks_source_down_but_serves(stack, keys, db_clean):
    stack.integration.commit(owner=OWN_A, platform="facebook",
                             resources=[res("pg1", "P", platform="facebook", managed=True, active=True)],
                             content=[post("p1", "pg1", "2026-10-01", 5)])
    url = stack.start(ANALYTICS_INTEGRATION_FEED_KEY="sai-khoa-" + "x" * 30)
    adm = stack.issuer.headers(ADMIN, "admin")
    wait_for(lambda: (httpx.get(url + "/v1/analytics/freshness", headers=adm, timeout=5).json()
                      ["sources"].get("integration") or {}).get("ok") is False)
    r = httpx.get(url + "/v1/analytics/summary", headers=stack.issuer.headers(OWN_A), timeout=5)
    assert r.status_code == 200 and r.json()["total_views"] == 0 and r.json()["freshness"]["stale"] is True
    assert "sai-khoa" not in stack.log.read_text(encoding="utf-8", errors="replace")
