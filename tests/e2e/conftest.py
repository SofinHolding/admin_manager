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
    s.reset_data()  # DB DEV riêng: dựng lại từ số 0 (khoá ký cũ mã hoá bằng khoá khác sẽ không giải mã được)
    s.migrate_all()
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


# ── Mock Tool X: một Tool HOÀN TOÀN MỚI (tên, nền tảng) — Manager không có dòng code nào biết nó ─────────────────────

import datetime as dt  # noqa: E402

from manager_sdk.integration_client import IntegrationClient  # noqa: E402

TOOL_NAME = "Mock Tool X"
TOOL_PLATFORM = "toolx_social"


def now_z() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(days_ago: int = 1) -> str:
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


class ToolX:
    """Tool ngoài dùng SDK chính thức qua gateway; chỉ biết base URL + API key."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url, self.api_key = base_url, api_key

    def run(self, coro_fn):
        async def go():
            client = IntegrationClient(self.base_url, self.api_key, retries=1, backoff_base=0.05, timeout=10)
            try:
                return await coro_fn(client)
            finally:
                await client.aclose()
        return asyncio.run(go())

    def handshake(self):
        return self.run(lambda c: c.handshake(device_id="toolx-dev-1", hostname="toolx-host", os_name="windows",
                                              app_version="1.0", tool_name=TOOL_NAME, platforms=[TOOL_PLATFORM],
                                              capabilities=["snapshots.push"]))

    def push(self, *, resources=(), content=(), metrics=(), events=(), snapshot_type="full", scope=None):
        ts = now_z()
        scope = scope if scope is not None else ({"resources": True} if snapshot_type == "full" else None)
        res = [{"resource_type": "channel", "state": "active", "observed_at": ts, **r} for r in resources]
        con = [{"observed_at": ts, **c} for c in content]
        met = [{"observed_at": ts, **m} for m in metrics]
        return self.run(lambda c: c.push_snapshot(platform=TOOL_PLATFORM, observed_at=ts, report_timezone="UTC",
                                                  snapshot_type=snapshot_type, scope=scope, resources=res, content=con,
                                                  metrics=met, events=list(events)))


@pytest.fixture(scope="session")
def world(stack, api):
    """Admin + chủ Tool đã đăng ký (kèm integration) + Mock Tool X có API key. Mọi thứ đi qua API công khai."""
    admin = api.user("e2eadmin", api.seed_invite_key(role="admin"))
    r = api.http.put(f"/v1/integrations/admin/platforms/{TOOL_PLATFORM}", headers=admin["headers"],
                     json={"display_name": "Tool X Social"})  # DỮ LIỆU registry, không phải code
    assert r.status_code == 200, r.text
    r = api.http.post("/v1/admin/keys", headers=admin["headers"], json={
        "role": "viewer", "max_uses": 3,
        "integration_policy": {"allow_integration_registration": True, "allowed_platforms": [TOOL_PLATFORM],
                               "max_integration_clients": 2}})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    owner = api.user("e2eowner", key, integration={"create": True, "tool_name": TOOL_NAME, "platforms": [TOOL_PLATFORM]})
    clients = wait_for(lambda: api.http.get("/v1/integrations/clients", headers=owner["headers"]).json()["data"],
                       what="Integration client được provisioning từ feed của Identity")
    cid = clients[0]["client_id"]
    r = api.http.post(f"/v1/integrations/clients/{cid}/credentials", headers=owner["headers"], json={})
    assert r.status_code == 201, r.text
    other = api.user("e2eother", api.seed_invite_key(role="viewer"))  # người dùng khác: không được thấy dữ liệu của owner
    return {"admin": admin, "owner": owner, "other": other, "client_id": cid, "client": clients[0],
            "tool": ToolX(stack.url("gateway"), r.json()["data"]["api_key"])}
