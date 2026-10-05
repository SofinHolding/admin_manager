"""Hạ tầng E2E: stack DEV bằng TIẾN TRÌNH THẬT (identity, integration, analytics, reward, gateway) + Postgres/Redis DEV riêng.

Không có hạ tầng DEV thì toàn bộ E2E SKIP (kèm lý do) — SKIP không phải PASS. Không dùng bất cứ dữ liệu/cổng nào ngoài `mp-dev-*`.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra" / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "sdk-python"))

import devenv  # noqa: E402

PASSWORD = "E2e-Passw0rd!" + secrets.token_hex(4)  # sinh mỗi lần chạy, không nằm trong repo


def pytest_collection_modifyitems(config, items):
    if devenv.pg_ready():
        return
    skip = pytest.mark.skip(reason=devenv.SKIP_REASON or "Postgres DEV chưa chạy (docker compose -f infra/docker/compose.dev.yml up -d)")
    for item in items:
        if "tests/e2e" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def stack():
    from dev_stack import DevStack
    s = DevStack()
    s.migrate_all()
    s.reset_data(("identity", "integration", "analytics"))  # DB DEV riêng: bắt đầu sạch (khoá ký cũ mã hoá bằng khoá khác sẽ không giải mã được)
    # reward tự migrate khi khởi động (REWARD_DB_MIGRATE_ON_BOOT=1)
    for name in ("identity", "integration", "analytics", "reward", "gateway"):
        s.start(name)
    yield s
    s.stop_all()


class Api:
    """Client HTTP đi qua GATEWAY (đường duy nhất người dùng/Tool thật dùng)."""

    def __init__(self, stack) -> None:
        self.stack = stack
        self.http = httpx.Client(base_url=stack.url("gateway"), timeout=15)

    def direct(self, service: str) -> httpx.Client:
        return httpx.Client(base_url=self.stack.url(service), timeout=15)

    # ── Người dùng ──────────────────────────────────────────────────────────────

    def seed_invite_key(self, *, role: str = "viewer", policy: dict | None = None, apps: list[str] | None = None) -> str:
        """Ops seed key đầu tiên thẳng vào identity_db (chưa có admin nào để gọi API)."""
        import json

        import asyncpg

        key = "e2e-" + secrets.token_hex(8)

        async def run():
            conn = await asyncpg.connect(devenv.pg_dsn("identity"))
            try:
                await conn.execute(
                    "INSERT INTO invite_keys (key, role, label, max_uses, expires_at) VALUES ($1,$2,'e2e',5, now()+interval '1 day')",
                    key, role)
                if policy is not None:
                    await conn.execute("UPDATE invite_keys SET integration_policy=$2::jsonb, allowed_apps=$3 WHERE key=$1",
                                       key, json.dumps(policy), apps or [])
            finally:
                await conn.close()
        asyncio.run(run())
        return key

    def register(self, key: str, username: str, *, integration: dict | None = None) -> httpx.Response:
        body = {"key": key, "username": username, "password": PASSWORD, "email": f"{username}@e2e.example.com"}
        if integration is not None:
            body["integration"] = integration
        return self.http.post("/v1/auth/register", json=body)

    def verify_email(self, username: str) -> None:
        deadline = time.time() + 10
        pattern = re.compile(rf"{re.escape(username)}@e2e.example.com: (\S+?)[\"\s}}]")
        while time.time() < deadline:
            m = pattern.findall(self.stack.log_text("identity"))
            if m:
                r = self.http.get("/v1/auth/verify-email", params={"token": m[-1]})
                assert r.status_code == 200, r.text
                return
            time.sleep(0.3)
        raise AssertionError("không thấy token xác thực email trong log DEV của identity")

    def login(self, username: str) -> dict:
        r = self.http.post("/v1/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()

    def bearer(self, username: str) -> dict:
        return {"Authorization": f"Bearer {self.login(username)['access_token']}"}

    def user(self, username: str, key: str, **kw) -> dict:
        r = self.register(key, username, **kw)
        assert r.status_code in (200, 201), r.text
        self.verify_email(username)
        return {"username": username, "headers": self.bearer(username), "register": r.json()}


@pytest.fixture(scope="session")
def api(stack):
    return Api(stack)


def wait_for(fn, *, timeout: float = 30.0, interval: float = 0.5, what: str = "điều kiện"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except (AssertionError, httpx.HTTPError):
            pass
        time.sleep(interval)
    raise AssertionError(f"quá hạn chờ {what} ({timeout}s); giá trị cuối: {last!r}")
