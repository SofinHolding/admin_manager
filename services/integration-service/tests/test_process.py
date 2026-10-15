"""Chạy như TIẾN TRÌNH THẬT (uvicorn): fail-fast cấu hình, DB chết, end-to-end qua HTTP thật, log JSON không lộ secret."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from conftest import ISSUER, REVEAL_KEY, devenv

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "services" / "integration-service"
PYPATH = os.pathsep.join([str(SERVICE / "src"), str(ROOT / "packages" / "observability" / "src"),
                          str(ROOT / "packages" / "contracts" / "python"), str(ROOT / "packages" / "sdk-python")])
FEED_KEY = "f" + uuid.uuid4().hex + uuid.uuid4().hex          # khoá Identity->Integration, sinh mỗi lần chạy
READER_KEY = "r" + uuid.uuid4().hex + uuid.uuid4().hex        # khoá bên đọc canonical, sinh mỗi lần chạy
DEAD_DSN = "postgresql://integration_svc:x@127.0.0.1:1/integration_db"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("INTEGRATION_")}
    return {**env, "PYTHONPATH": PYPATH, "PYTHONIOENCODING": "utf-8", **extra}


def run_py(code: str, **extra) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=SERVICE, env=clean_env(**extra), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


class FakeIdentityServer:
    """HTTP server thật phục vụ JWKS + feed (đúng hợp đồng) để tiến trình Integration gọi qua mạng."""

    def __init__(self, identity) -> None:
        self.identity = identity
        self.events: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # im lặng
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urlparse(self.path)
                if url.path == "/.well-known/jwks.json":
                    return self._send(200, outer.identity.jwks())
                if url.path == "/internal/v1/feed":
                    if self.headers.get("X-Service-Key") != FEED_KEY:
                        return self._send(401, {"detail": "no"})
                    q = parse_qs(url.query)
                    after = int(q.get("after", ["0"])[0])
                    limit = int(q.get("limit", ["100"])[0])
                    rows = [e for e in outer.events if e["seq"] > after][:limit]
                    return self._send(200, {"events": rows, "next_cursor": rows[-1]["seq"] if rows else after,
                                            "has_more": False, "head": len(outer.events)})
                self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, type_: str, data: dict) -> None:
        seq = len(self.events) + 1
        self.events.append({"seq": seq, "id": f"evt_{uuid.uuid4().hex}", "type": type_, "subject": "acc:x",
                            "time": "2026-10-05T10:00:00Z", "data_version": 1, "data": data})

    def close(self) -> None:
        self.server.shutdown()


class Proc:
    def __init__(self, tmp_path: Path, **env) -> None:
        self.port = free_port()
        self.log = tmp_path / f"svc-{self.port}.log"
        self.env = clean_env(INTEGRATION_ENV="test", INTEGRATION_ISSUER=ISSUER, INTEGRATION_MAINTENANCE_INTERVAL_S="0",
                             **env)
        self.popen: subprocess.Popen | None = None

    def start(self) -> "Proc":
        fh = open(self.log, "wb")
        self.popen = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "integration_service.main:app", "--host", "127.0.0.1", "--port",
             str(self.port)], cwd=SERVICE, env=self.env, stdout=fh, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.popen.poll() is not None:
                raise RuntimeError(f"tiến trình thoát sớm ({self.popen.returncode}): {self.logs()[-1500:]}")
            try:
                if httpx.get(self.url("/health/live"), timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.15)
        raise TimeoutError("service không sống sau 30s")

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def stop(self) -> None:
        if self.alive():
            self.popen.terminate()
            try:
                self.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.popen.kill()

    def logs(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""


# ── Không cần DB ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env,needle", [
    ({"INTEGRATION_REVEAL_KEY": REVEAL_KEY}, "INTEGRATION_DATABASE_URL"),
    ({"INTEGRATION_DATABASE_URL": DEAD_DSN, "INTEGRATION_REVEAL_KEY": REVEAL_KEY}, "INTEGRATION_ISSUER"),
    ({"INTEGRATION_DATABASE_URL": DEAD_DSN, "INTEGRATION_ISSUER": ISSUER, "INTEGRATION_REVEAL_KEY": REVEAL_KEY},
     "INTEGRATION_IDENTITY_JWKS_URL"),
    ({"INTEGRATION_DATABASE_URL": DEAD_DSN, "INTEGRATION_ISSUER": ISSUER, "INTEGRATION_IDENTITY_JWKS_URL": "http://x/j",
      "INTEGRATION_REVEAL_KEY": REVEAL_KEY, "INTEGRATION_ENV": "production"}, "https://"),
    ({"INTEGRATION_DATABASE_URL": "sqlite:///x.db"}, "PostgreSQL"),
    ({"INTEGRATION_DATABASE_URL": DEAD_DSN, "INTEGRATION_ISSUER": ISSUER, "INTEGRATION_IDENTITY_JWKS_URL": "http://x/j",
      "INTEGRATION_FEED_KEY_ANALYTICS": "ngan"}, "INTEGRATION_FEED_KEY_ANALYTICS"),
])
def test_service_refuses_to_start_without_required_config(env, needle):
    proc = run_py("import integration_service.main", **env)
    assert proc.returncode != 0 and needle in proc.stderr


def test_migrate_job_fails_fast_on_missing_config_and_dead_database():
    cfg = run_py("import sys; from integration_service import migrate; sys.exit(migrate.main())",
                 INTEGRATION_REVEAL_KEY=REVEAL_KEY)
    assert cfg.returncode == 2 and "INTEGRATION_DATABASE_URL" in cfg.stderr
    dead = run_py("import sys; from integration_service import migrate; sys.exit(migrate.main())",
                  INTEGRATION_DATABASE_URL=DEAD_DSN, INTEGRATION_REVEAL_KEY=REVEAL_KEY)
    assert dead.returncode == 1


def test_service_with_dead_database_stays_alive_and_fails_fast_with_503(tmp_path):
    p = Proc(tmp_path, INTEGRATION_DATABASE_URL=DEAD_DSN, INTEGRATION_IDENTITY_JWKS_URL="http://127.0.0.1:1/jwks",
             INTEGRATION_REVEAL_KEY=REVEAL_KEY).start()
    try:
        assert httpx.get(p.url("/health/live"), timeout=2).status_code == 200
        ready = httpx.get(p.url("/health/ready"), timeout=5)
        assert ready.status_code == 503 and ready.json()["checks"] == {"database": False}
        started = time.time()
        key = "Bearer mpk_" + "a" * 12 + "." + "b" * 43
        r = httpx.post(p.url("/v1/integrations/handshake"), headers={"Authorization": key},
                       json={"installation": {"device_id": "d"}}, timeout=20)
        assert r.status_code == 503 and r.json() == {"ok": False, "error": {
            "code": "UNAVAILABLE", "message": "Dịch vụ tạm thời không khả dụng", "details": {}},
            "meta": {"request_id": r.headers["x-request-id"]}}
        assert time.time() - started < 12, "DB chết phải thất bại NHANH, không treo tới timeout mặc định"
        assert p.alive() and httpx.get(p.url("/health/live"), timeout=2).status_code == 200
        # lỗi user-plane khi Identity cũng chết: 401 (không treo, không 500)
        r = httpx.get(p.url("/v1/integrations/clients"), headers={"Authorization": "Bearer abc.def.ghi"}, timeout=10)
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID"
        assert httpx.get(p.url("/openapi.json"), timeout=5).status_code == 200
    finally:
        p.stop()
    assert "mpk_" not in p.logs()


# ── End-to-end qua HTTP thật ────────────────────────────────────────────────────

@pytest.mark.needs_pg
async def test_end_to_end_as_a_real_process_with_identity_provisioning(db, identity, tmp_path):
    fake = FakeIdentityServer(identity)
    dsn = devenv.pg_dsn("integration")
    password = dsn.split("://")[1].split(":")[1].split("@")[0]
    account = uuid.uuid4().hex
    fake.add("account.registered", {"account_id": account, "username": "tool_owner", "role": "viewer", "status": "pending"})
    p = Proc(tmp_path, INTEGRATION_DATABASE_URL=dsn, INTEGRATION_IDENTITY_JWKS_URL=f"{fake.url}/.well-known/jwks.json",
             INTEGRATION_FEED_KEY_ANALYTICS=READER_KEY, INTEGRATION_IDENTITY_FEED_URL=fake.url,
             INTEGRATION_IDENTITY_FEED_KEY=FEED_KEY, INTEGRATION_PROVISIONING_POLL_S="0.1",
             INTEGRATION_REVEAL_KEY=REVEAL_KEY)
    mig = subprocess.run([sys.executable, "-m", "integration_service.migrate"], cwd=SERVICE, env=p.env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert mig.returncode == 0, mig.stderr
    p.start()
    try:
        admin = {"Authorization": f"Bearer {identity.token(sub='acc_' + uuid.uuid4().hex, role='admin')}"}
        user = {"Authorization": f"Bearer {identity.token(sub=account, role='viewer', perms=['capability:tool_integration'])}"}

        def wait_for(predicate, what, timeout=20.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                result = predicate()
                if result:
                    return result
                time.sleep(0.2)
            raise AssertionError(f"quá hạn chờ: {what}\n{p.logs()[-2000:]}")

        def client_id_of():
            r = httpx.get(p.url("/v1/integrations/clients"), headers=admin, params={"owner": account}, timeout=5)
            items = r.json()["data"]["items"] if r.status_code == 200 else []
            return items[0]["client_id"] if items else None

        time.sleep(0.5)
        assert client_id_of() is None, "chưa có capability:tool_integration thì chưa được tạo client cá nhân"

        fake.add("account.permission_changed",
                 {"account_id": account, "permission": "capability:tool_integration", "granted": True})
        client_id = wait_for(client_id_of, "client cá nhân được provision từ capability:tool_integration")

        def client_status():
            r = httpx.get(p.url(f"/v1/integrations/clients/{client_id}"), headers=user, timeout=5)
            return r.json()["data"]["status"] if r.status_code == 200 else None

        assert wait_for(client_status, "client ở trạng thái chờ xác minh email") == "pending_email_verification"
        r = httpx.post(p.url("/v1/integrations/me/credential/reveal"), headers=user, json={}, timeout=5)
        assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
        fake.add("account.verified", {"account_id": account})
        wait_for(lambda: client_status() == "active", "client active sau account.verified")

        r = httpx.post(p.url("/v1/integrations/me/credential/reveal"), headers=user, json={}, timeout=5)
        assert r.status_code == 200, r.text
        api_key = r.json()["data"]["api_key"]
        tool = {"Authorization": f"Bearer {api_key}"}
        hs = httpx.post(p.url("/v1/integrations/handshake"), headers=tool, json={"installation": {"device_id": "e2e"}}, timeout=5)
        assert hs.status_code == 200 and hs.json()["data"]["owner_account_id"] == account
        tool = {**tool, "X-Installation-Id": hs.json()["data"]["installation_id"]}

        snap = httpx.post(p.url("/v1/integrations/snapshots"), headers={**tool, "Idempotency-Key": "e2e-" + uuid.uuid4().hex},
                          timeout=5, json={"platform": "facebook", "snapshot_type": "full", "observed_at": "2026-10-05T10:00:00Z",
                                           "report_timezone": "Asia/Ho_Chi_Minh", "scope": {"resources": True},
                                           "expected_counts": {"resources": 1, "content": 1}})
        sid = snap.json()["data"]["snapshot_id"]
        base = p.url(f"/v1/integrations/snapshots/{sid}")
        assert httpx.post(base + "/resources", headers=tool, timeout=5, json={"items": [
            {"external_id": "page1", "resource_type": "page", "observed_at": "2026-10-05T10:00:00Z"}]}).status_code == 200
        assert httpx.post(base + "/content", headers=tool, timeout=5, json={"items": [
            {"external_id": "post1", "resource_external_id": "page1", "published_date": "2026-10-04",
             "metrics": {"views": 42, "reactions": None}, "observed_at": "2026-10-05T10:00:00Z"}]}).status_code == 200
        done = httpx.post(base + "/commit", headers=tool, json={}, timeout=10)
        assert done.status_code == 200 and done.json()["data"]["status"] == "committed"

        reader = {"X-Service-Key": READER_KEY}
        content = httpx.get(p.url("/internal/v1/canonical/content"), headers=reader, timeout=5).json()["items"]
        assert content[0]["metrics"] == {"views": 42, "reactions": None} and content[0]["owner_account_id"] == account
        types = [e["type"] for e in httpx.get(p.url("/internal/v1/feed"), headers=reader, timeout=5).json()["events"]]
        assert "snapshot.committed" in types and types.count("integration.client.provisioned") == 2
        assert httpx.get(p.url("/internal/v1/feed"), timeout=5).status_code == 401
        assert httpx.get(p.url("/health/ready"), timeout=5).status_code == 200
        owner_key = api_key.split(".")[1]
    finally:
        p.stop()
        fake.close()

    lines = [ln for ln in p.logs().splitlines() if ln.strip()]
    assert lines, "tiến trình không ghi log nào"
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except ValueError:
            pytest.fail(f"dòng log không phải JSON: {ln!r}")
    assert all({"ts", "level", "service_name", "message"} <= set(rec) for rec in parsed)
    assert {rec["service_name"] for rec in parsed} == {"integration-service"}
    blob = p.logs()
    for secret in (api_key, owner_key, FEED_KEY, READER_KEY, password):
        assert secret not in blob, "secret xuất hiện trong log"
    assert "Authorization" not in blob and "authorization" not in blob.lower().replace("authorization header", "")


@pytest.mark.needs_pg
async def test_real_process_reports_not_ready_until_migrated_then_ready(db, identity, tmp_path):
    fake = FakeIdentityServer(identity)
    try:
        async with db.connection() as conn:     # mô phỏng DB chưa migrate: đổi tên bảng migration
            await conn.execute("ALTER TABLE schema_migrations RENAME TO schema_migrations_off")
        p = Proc(tmp_path, INTEGRATION_DATABASE_URL=devenv.pg_dsn("integration"),
                 INTEGRATION_IDENTITY_JWKS_URL=f"{fake.url}/.well-known/jwks.json", INTEGRATION_REVEAL_KEY=REVEAL_KEY).start()
        try:
            assert httpx.get(p.url("/health/ready"), timeout=5).status_code == 503
            async with db.connection() as conn:
                await conn.execute("ALTER TABLE schema_migrations_off RENAME TO schema_migrations")
            assert httpx.get(p.url("/health/ready"), timeout=5).status_code == 200
            assert httpx.get(p.url("/health/version"), timeout=5).json()["service"] == "integration-service"
        finally:
            p.stop()
    finally:
        async with db.connection() as conn:
            if await conn.fetchval("SELECT to_regclass('schema_migrations_off')"):
                await conn.execute("ALTER TABLE schema_migrations_off RENAME TO schema_migrations")
        fake.close()
