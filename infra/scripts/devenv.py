"""Truy cập cấu hình DEV (đọc `.env.dev` do bootstrap_dev.py sinh). Dùng cho test và script, KHÔNG dùng ở runtime service.

    import devenv; devenv.load()
    dsn = devenv.pg_dsn("identity")            # user riêng của service
    admin = devenv.pg_dsn(admin=True)          # chỉ để dựng/xoá database mô phỏng trong test
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap_dev  # noqa: E402

ROOT = bootstrap_dev.ROOT
SKIP_REASON = ("Chưa có hạ tầng DEV: chạy `python infra/scripts/bootstrap_dev.py` rồi "
               "`docker compose --env-file .env.dev -f infra/docker/compose.dev.yml up -d`. "
               "SKIP KHÔNG phải là đã kiểm chứng.")


def load() -> dict[str, str]:
    return bootstrap_dev.load_into_environ()


def _get(name: str) -> str:
    load()
    return os.environ[name]


def pg_host_port() -> tuple[str, int]:
    load()
    return os.environ.get("DEV_PG_HOST", "127.0.0.1"), int(os.environ.get("DEV_PG_PORT", "55440"))


def pg_dsn(service: str = "", *, admin: bool = False, database: str | None = None) -> str:
    host, port = pg_host_port()
    if admin:
        return f"postgresql://postgres:{_get('DEV_PG_ADMIN_PASSWORD')}@{host}:{port}/{database or 'postgres'}"
    return (f"postgresql://{service}_svc:{_get('DEV_PG_' + service.upper() + '_PASSWORD')}"
            f"@{host}:{port}/{database or service + '_db'}")


def redis_url() -> str:
    load()
    return (f"redis://analytics_cache:{_get('DEV_REDIS_ANALYTICS_PASSWORD')}@"
            f"{os.environ.get('DEV_REDIS_HOST', '127.0.0.1')}:{os.environ.get('DEV_REDIS_PORT', '26390')}/0")


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def have_env_file() -> bool:
    return bootstrap_dev.ENV_FILE.exists()


def pg_ready() -> bool:
    if not have_env_file():
        return False
    host, port = pg_host_port()
    return _reachable(host, port)


def redis_ready() -> bool:
    if not have_env_file():
        return False
    load()
    return _reachable(os.environ.get("DEV_REDIS_HOST", "127.0.0.1"), int(os.environ.get("DEV_REDIS_PORT", "26390")))
