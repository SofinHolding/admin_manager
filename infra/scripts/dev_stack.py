"""Dựng stack DEV bằng TIẾN TRÌNH THẬT (mỗi service một process) để chạy E2E / kiểm thử cô lập lỗi.

Chỉ dùng hạ tầng DEV riêng (`mp-dev-postgres` 55440, `mp-dev-redis` 56390) và cổng loopback ngẫu nhiên;
không đụng bất cứ container/cổng nào khác. Secret lấy từ `.env.dev` (bootstrap_dev.py), không có giá trị cứng.

Dùng như thư viện trong test:
    stack = DevStack(); stack.migrate_all(); stack.start("identity"); ...
hoặc CLI:  python infra/scripts/dev_stack.py up|down  (up giữ tiến trình tới Ctrl+C)
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra" / "scripts"))
import bootstrap_dev  # noqa: E402
import devenv  # noqa: E402

SERVICE_DIRS = {
    "identity": ("services/identity-service", "identity_service"),
    "integration": ("services/integration-service", "integration_service"),
    "analytics": ("services/analytics-service", "analytics_service"),
    "gateway": ("services/api-gateway", "api_gateway"),
    "reward": ("reward-service", None),
}
MIGRATABLE = ("identity", "integration", "analytics")
COMMON_PATHS = ["packages/observability/src", "packages/contracts/python", "packages/sdk-python"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DevStack:
    def __init__(self, *, redis: bool = True) -> None:
        bootstrap_dev.load_into_environ()
        self.ports = {name: free_port() for name in SERVICE_DIRS}
        self.procs: dict[str, subprocess.Popen] = {}
        self.use_redis = redis and bool(devenv.redis_ready())
        self.overrides: dict[str, dict[str, str]] = {}  # ghi đè env theo service (để mô phỏng DB chết, Redis chết...)

    # ── URL / env ───────────────────────────────────────────────────────────────

    def url(self, name: str, path: str = "") -> str:
        return f"http://127.0.0.1:{self.ports[name]}{path}"

    def _env(self, name: str) -> dict[str, str]:
        e = os.environ
        identity = self.url("identity")
        base: dict[str, str] = {}
        if name == "identity":
            base = {"IDENTITY_PORT": str(self.ports[name]), "IDENTITY_ENV": "dev", "IDENTITY_ISSUER": identity,
                    "IDENTITY_DATABASE_URL": devenv.pg_dsn("identity"),
                    "IDENTITY_KEY_ENCRYPTION_KEY": e["IDENTITY_KEY_ENCRYPTION_KEY"],
                    "IDENTITY_INTEGRATION_FEED_URL": self.url("integration"),
                    "IDENTITY_INTEGRATION_FEED_KEY": e["INTEGRATION_FEED_KEY_IDENTITY"],
                    "IDENTITY_FEED_KEY_ANALYTICS": e["IDENTITY_FEED_KEY_ANALYTICS"],
                    "IDENTITY_FEED_KEY_INTEGRATION": e["IDENTITY_FEED_KEY_INTEGRATION"],
                    "IDENTITY_FEED_KEY_REWARD": e["IDENTITY_FEED_KEY_REWARD"]}
        elif name == "integration":
            base = {"INTEGRATION_PORT": str(self.ports[name]), "INTEGRATION_ENV": "dev",
                    "INTEGRATION_DATABASE_URL": devenv.pg_dsn("integration"), "INTEGRATION_ISSUER": identity,
                    "INTEGRATION_IDENTITY_JWKS_URL": identity + "/.well-known/jwks.json",
                    "INTEGRATION_FEED_KEY_ANALYTICS": e["INTEGRATION_FEED_KEY_ANALYTICS"],
                    "INTEGRATION_FEED_KEY_IDENTITY": e["INTEGRATION_FEED_KEY_IDENTITY"],
                    "INTEGRATION_IDENTITY_FEED_URL": identity,
                    "INTEGRATION_IDENTITY_FEED_KEY": e["IDENTITY_FEED_KEY_INTEGRATION"],
                    "INTEGRATION_PROVISIONING_POLL_S": "1"}
        elif name == "analytics":
            base = {"ANALYTICS_PORT": str(self.ports[name]), "ANALYTICS_ENV": "dev",
                    "ANALYTICS_DATABASE_URL": devenv.pg_dsn("analytics"), "ANALYTICS_ISSUER": identity,
                    "ANALYTICS_IDENTITY_JWKS_URL": identity + "/.well-known/jwks.json",
                    "ANALYTICS_INTEGRATION_URL": self.url("integration"),
                    "ANALYTICS_INTEGRATION_FEED_KEY": e["INTEGRATION_FEED_KEY_ANALYTICS"],
                    "ANALYTICS_IDENTITY_URL": identity, "ANALYTICS_IDENTITY_FEED_KEY": e["IDENTITY_FEED_KEY_ANALYTICS"],
                    "ANALYTICS_SYNC_INTERVAL_S": "1"}
            if self.use_redis:
                base["ANALYTICS_REDIS_URL"] = devenv.redis_url()
        elif name == "reward":
            base = {"REWARD_PORT": str(self.ports[name]), "REWARD_DATABASE_URL": devenv.pg_dsn("reward"),
                    "REWARD_IDENTITY_URL": identity, "REWARD_IDENTITY_JWKS_URL": identity + "/.well-known/jwks.json",
                    "REWARD_ISSUER": identity, "REWARD_AUDIENCE": "manager-api",
                    "REWARD_IDENTITY_FEED_URL": identity, "REWARD_IDENTITY_FEED_KEY": e["IDENTITY_FEED_KEY_REWARD"],
                    "REWARD_FEED_POLL_INTERVAL_S": "1", "REWARD_TOKEN_KEY": e.get("REWARD_TOKEN_KEY", "")}
        elif name == "gateway":
            base = {"GATEWAY_ENV": "dev", "GATEWAY_RATE_LIMIT_ENABLED": "false",
                    **{f"GATEWAY_UPSTREAM_{u.upper()}": self.url(u) for u in ("identity", "integration", "analytics", "reward")}}
        return {**base, **self.overrides.get(name, {})}

    def _pythonpath(self, name: str) -> str:
        d, _ = SERVICE_DIRS[name]
        paths = [str(ROOT / d / "src")] if name != "reward" else [str(ROOT / d)]
        paths += [str(ROOT / p) for p in COMMON_PATHS]
        return os.pathsep.join(paths)

    def _full_env(self, name: str) -> dict[str, str]:
        env = {**os.environ, **self._env(name), "PYTHONPATH": self._pythonpath(name), "PYTHONIOENCODING": "utf-8"}
        return {k: v for k, v in env.items() if v is not None and not (k.endswith("_FEED_KEY") and v == "")}

    # ── Vòng đời ────────────────────────────────────────────────────────────────

    def migrate(self, name: str) -> None:
        d, pkg = SERVICE_DIRS[name]
        r = subprocess.run([sys.executable, "-m", f"{pkg}.migrate"], cwd=ROOT / d, env=self._full_env(name),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"migrate {name} lỗi: {(r.stderr or r.stdout)[-500:]}")

    def migrate_all(self) -> None:
        for n in MIGRATABLE:
            self.migrate(n)

    def reset_data(self, names: tuple[str, ...] = MIGRATABLE + ("reward",)) -> None:
        """Dựng lại DB DEV của từng service từ số 0: xoá MỌI đối tượng do user của service sở hữu, rồi gọi `migrate_all()`.

        (Không TRUNCATE: dữ liệu seed của migration như Platform/Metric Registry và catalog permission phải được dựng lại.)
        Chỉ chạm DB `<service>_db` bằng chính user của service (không có đường sang DB khác)."""
        import asyncio

        import asyncpg

        async def run(svc: str) -> None:
            conn = await asyncpg.connect(devenv.pg_dsn(svc))
            try:
                await conn.execute(f"DROP OWNED BY {svc}_svc CASCADE")
            finally:
                await conn.close()

        for svc in names:
            asyncio.run(run(svc))

    def start(self, name: str, *, wait: float = 30.0) -> None:
        if self.alive(name):
            return
        d, pkg = SERVICE_DIRS[name]
        if name == "reward":
            cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(self.ports[name]),
                   "--workers", "1", "--log-level", "warning"]
        else:
            cmd = [sys.executable, "-m", "uvicorn", f"{pkg}.main:app", "--host", "127.0.0.1", "--port",
                   str(self.ports[name]), "--log-level", "warning", "--no-access-log"]
        log_dir = ROOT / ".dev" / "logs"  # .dev/ nằm trong .gitignore
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / f"{name}.log", "ab")
        self.procs[name] = subprocess.Popen(cmd, cwd=ROOT / d, env=self._full_env(name), stdout=log, stderr=log)
        log.close()  # tiến trình con giữ handle riêng
        self._wait_live(name, wait)

    def _wait_live(self, name: str, timeout: float) -> None:
        deadline = time.time() + timeout
        path = "/v1/reward/health" if name == "reward" else "/health/live"
        while time.time() < deadline:
            if self.procs[name].poll() is not None:
                raise RuntimeError(f"{name} thoát sớm, mã {self.procs[name].returncode}")
            try:
                if httpx.get(self.url(name, path), timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise TimeoutError(f"{name} không sống sau {timeout}s")

    def alive(self, name: str) -> bool:
        p = self.procs.get(name)
        return p is not None and p.poll() is None

    def stop(self, name: str) -> None:
        p = self.procs.get(name)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    def restart(self, name: str, **override_env: str) -> None:
        self.stop(name)
        if override_env:
            self.overrides.setdefault(name, {}).update(override_env)
        self.start(name)

    def stop_all(self) -> None:
        for name in list(self.procs):
            self.stop(name)

    def pid(self, name: str) -> int:
        return self.procs[name].pid

    def log_text(self, name: str) -> str:
        path = ROOT / ".dev" / "logs" / f"{name}.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def ready_status(self, name: str) -> int | None:
        try:
            return httpx.get(self.url(name, "/health/ready"), timeout=3).status_code
        except httpx.HTTPError:
            return None


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "up"
    if cmd != "up":
        print("dùng: dev_stack.py up  (Ctrl+C để dừng)")
        return 2
    if not devenv.pg_ready():
        print("Postgres DEV chưa chạy: docker compose -f infra/docker/compose.dev.yml up -d", file=sys.stderr)
        return 1
    stack = DevStack()
    try:
        stack.migrate_all()
        for n in ("identity", "integration", "analytics", "reward", "gateway"):
            stack.start(n)
            print(f"{n:12s} {stack.url(n)}")
        print("Stack đang chạy; Ctrl+C để dừng.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        stack.stop_all()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
