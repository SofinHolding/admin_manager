#!/usr/bin/env python3
"""Dựng/kiểm tra khung service theo infra/service-registry.json.

  python infra/scripts/scaffold_service.py            # tạo file còn thiếu (KHÔNG BAO GIỜ ghi đè file có sẵn)
  python infra/scripts/scaffold_service.py --check    # chỉ báo file còn thiếu, thoát 1 nếu thiếu
  python infra/scripts/scaffold_service.py --service identity-service

Quy trình thêm module/service: (1) sửa registry, (2) chạy script này, (3) viết code và test trong module.
Chỉ xử lý service có status = "scaffold". Service `status: active` (vd reward-service: đã theo skeleton chuẩn nhưng không do scaffold sinh) không bị đụng.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "infra" / "service-registry.json"

# ── Mẫu file ────────────────────────────────────────────────────────────────────

README = Template("""# $name

$responsibility

| | |
|---|---|
| Cổng | $port |
| Database (riêng) | `$database` |
| Package | `$package` |
| Trạng thái | scaffold (chưa có nghiệp vụ; module được hiện thực theo phase) |

## Module

| Module | Phase | Trách nhiệm |
|---|---|---|
$module_rows

## Dữ liệu và sự kiện (theo `infra/service-registry.json`)

- Bảng sở hữu: $tables
- Phát event: $emits
- Nhận event: $consumes

## Chạy

```bash
cd $path
python -m pytest -q                  # test riêng của service
python -m uvicorn $package.main:app --port $port
```

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
""")

REQUIREMENTS = """fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
"""

ENV_EXAMPLE = Template("""# $name — biến môi trường RIÊNG của service (không dùng chung file .env với service khác)
${prefix}_PORT=$port
${prefix}_LOG_LEVEL=INFO
# Database RIÊNG. Mỗi service có user và mật khẩu riêng, chỉ có quyền trên $database.
${prefix}_DATABASE_URL=postgresql://$database_user:CHANGE_ME@127.0.0.1:5432/$database
$redis_line""")

PYTEST_INI = """[pytest]
pythonpath = src ../../packages/observability/src ../../packages/contracts/python ../../packages/sdk-python
testpaths = tests
asyncio_mode = auto
"""

DOCKERFILE = Template("""# Build từ THƯ MỤC GỐC repo:  docker build -f $path/Dockerfile -t $name .
FROM python:3.12-slim
WORKDIR /app
COPY $path/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY packages/contracts/python /app/packages/contracts/python
COPY packages/observability/src /app/packages/observability/src
COPY packages/sdk-python /app/packages/sdk-python
COPY $path/src /app/src
COPY $path/migrations /app/migrations
RUN useradd --system --uid 10001 --no-create-home app && chown -R app /app
USER app
ENV PYTHONPATH=/app/src:/app/packages/observability/src:/app/packages/contracts/python:/app/packages/sdk-python
EXPOSE $port
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$port/health/live').status==200 else 1)"
CMD ["uvicorn", "$package.main:app", "--host", "0.0.0.0", "--port", "$port"]
""")

MIGRATIONS_README = Template("""# Migration của $name

- Chỉ chứa DDL cho database `$database` của CHÍNH service này.
- Tên file: `NNNN_mo_ta.sql`, chạy tăng dần, forward-only. Không sửa file đã áp dụng.
- Mọi bảng tạo ở đây phải có trong `owned_tables` của registry; không tạo bảng của service khác.
- Chạy như một job riêng, không chạy ngầm lúc service khởi động.
""")

INIT_PKG = '''"""$name — chassis. Phiên bản dịch vụ."""

__version__ = "0.1.0"
SERVICE_NAME = "$name"
'''

CONFIG = Template('''"""Cấu hình từ biến môi trường (`${prefix}_*`). Sai cấu hình thì fail-fast bằng ConfigError."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """Cấu hình thiếu hoặc sai định dạng — service không được khởi động."""


@dataclass(frozen=True)
class Settings:
    port: int
    log_level: str
    database_url: str
$redis_field

def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get("${prefix}_" + name, default) or "").strip()


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    raw_port = _env(env, "PORT", "$port")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigError("${prefix}_PORT phải là số nguyên") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("${prefix}_PORT phải trong khoảng 1..65535")

    level = _env(env, "LOG_LEVEL", "INFO").upper()
    if level not in _LOG_LEVELS:
        raise ConfigError("${prefix}_LOG_LEVEL không hợp lệ")

    database_url = _env(env, "DATABASE_URL")
    if database_url and not database_url.startswith(("postgresql://", "postgres://")):
        raise ConfigError("${prefix}_DATABASE_URL phải là PostgreSQL")
$redis_load
    return Settings(port=port, log_level=level, database_url=database_url$redis_ctor)
''')

APP = Template('''"""Dựng ứng dụng: chassis chuẩn + (các router của module sẽ được gắn ở đây theo phase)."""

from __future__ import annotations

from fastapi import FastAPI
from manager_observability import create_service_app

from $package import SERVICE_NAME, __version__


def create_app() -> FastAPI:
    # Readiness chỉ kiểm tài nguyên CỦA CHÍNH service (DB riêng). Thêm check ở đây khi có kết nối DB.
    return create_service_app(service_name=SERVICE_NAME, service_version=__version__,
                              readiness_checks={})
''')

MAIN = Template('''"""Entry point: `uvicorn $package.main:app`."""

from manager_observability import configure_logging

from $package import SERVICE_NAME, __version__
from $package.app import create_app
from $package.config import load_settings

_settings = load_settings()
configure_logging(SERVICE_NAME, __version__, _settings.log_level)
app = create_app()
''')

MODULE_INIT = Template('''"""module: $module
service: $name
phase: $phase
status: planned
responsibility: $responsibility
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).
"""
''')

TEST_HEALTH = Template('''"""Test chassis của $name: health, request-id, envelope lỗi."""

from fastapi.testclient import TestClient

from $package import SERVICE_NAME
from $package.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_live_and_ready():
    c = _client()
    assert c.get("/health/live").json() == {"status": "live", "service": SERVICE_NAME}
    assert c.get("/health/ready").status_code == 200


def test_version_reports_contract_version():
    body = _client().get("/health/version").json()
    assert body["service"] == SERVICE_NAME and body["contract_version"] == "2026-10-01"


def test_unknown_route_uses_stable_error_envelope():
    r = _client().get("/v1/khong-ton-tai")
    body = r.json()
    assert r.status_code == 404 and body["ok"] is False and body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["request_id"] == r.headers["X-Request-Id"]


def test_openapi_is_served():
    spec = _client().get("/openapi.json").json()
    assert spec["info"]["title"] == SERVICE_NAME
''')

TEST_CONFIG = Template('''"""Test cấu hình của $name: mặc định hợp lệ và fail-fast khi sai."""

import pytest

from $package.config import ConfigError, load_settings


def test_defaults_are_valid():
    s = load_settings({})
    assert s.port == $port and s.log_level == "INFO" and s.database_url == ""


def test_env_overrides():
    s = load_settings({"${prefix}_PORT": "9001", "${prefix}_LOG_LEVEL": "debug",
                       "${prefix}_DATABASE_URL": "postgresql://u:p@h:5432/$database"})
    assert s.port == 9001 and s.log_level == "DEBUG"


@pytest.mark.parametrize("env", [
    {"${prefix}_PORT": "abc"},
    {"${prefix}_PORT": "0"},
    {"${prefix}_PORT": "70000"},
    {"${prefix}_LOG_LEVEL": "LOUD"},
    {"${prefix}_DATABASE_URL": "sqlite:///x.db"},
    {"${prefix}_DATABASE_URL": "mysql://u:p@h/db"},
])
def test_invalid_config_fails_fast(env):
    with pytest.raises(ConfigError):
        load_settings(env)
''')


# ── Dựng ────────────────────────────────────────────────────────────────────────

def _prefix(name: str) -> str:
    return name.replace("-service", "").replace("-", "_").upper()


def planned_files(name: str, svc: dict) -> dict[str, str]:
    prefix = _prefix(name)
    has_cache = "cache" in svc["modules"]
    ctx = {
        "name": name, "package": svc["package"], "port": svc["port"],
        "database_user": (svc["database"] or "").replace("_db", "_svc"), "path": svc["path"],
        "prefix": prefix, "responsibility": svc["responsibility"],
        "tables": ", ".join(f"`{t}`" for t in svc["owned_tables"]) or "—",
        "emits": ", ".join(f"`{e}`" for e in svc["emits"]) or "—",
        "consumes": ", ".join(f"`{e}`" for e in svc["consumes"]) or "—",
        "database": svc["database"] or "—",
        "module_rows": "\n".join(
            f"| `{m}` | {v['phase']} | {v['responsibility']} |" for m, v in svc["modules"].items()),
        "redis_line": f"{prefix}_REDIS_URL=redis://127.0.0.1:6379/0  # cache thuần; ACL user riêng\n" if has_cache else "",
        "redis_field": "    redis_url: str\n" if has_cache else "",
        "redis_load": f'    redis_url = _env(env, "REDIS_URL")\n' if has_cache else "",
        "redis_ctor": ", redis_url=redis_url" if has_cache else "",
    }
    pkg = f"{svc['path']}/src/{svc['package']}"
    has_db = bool(svc["database"])
    dockerfile = DOCKERFILE.substitute(ctx)
    env_example = ENV_EXAMPLE.substitute(ctx)
    if not has_db:  # service không trạng thái (vd gateway): không migration, không DB
        dockerfile = "\n".join(ln for ln in dockerfile.splitlines() if "/migrations" not in ln) + "\n"
        env_example = "\n".join(ln for ln in env_example.splitlines() if "DATABASE" not in ln
                                 and "Database RIÊNG" not in ln) + "\n"
    files = {
        f"{svc['path']}/README.md": README.substitute(ctx),
        f"{svc['path']}/requirements.txt": REQUIREMENTS,
        f"{svc['path']}/.env.example": env_example,
        f"{svc['path']}/pytest.ini": PYTEST_INI,
        f"{svc['path']}/Dockerfile": dockerfile,
        f"{pkg}/__init__.py": Template(INIT_PKG).substitute(ctx),
        f"{pkg}/config.py": CONFIG.substitute(ctx),
        f"{pkg}/app.py": APP.substitute(ctx),
        f"{pkg}/main.py": MAIN.substitute(ctx),
        f"{svc['path']}/tests/test_health.py": TEST_HEALTH.substitute(ctx),
        f"{svc['path']}/tests/test_config.py": TEST_CONFIG.substitute(ctx),
    }
    if has_db:
        files[f"{svc['path']}/migrations/README.md"] = MIGRATIONS_README.substitute(ctx)
    for module, meta in svc["modules"].items():
        files[f"{pkg}/{module}/__init__.py"] = MODULE_INIT.substitute(
            module=module, name=name, phase=meta["phase"], responsibility=meta["responsibility"])
    return files


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def scaffold_targets(registry: dict, only: str | None = None) -> dict[str, dict]:
    return {n: s for n, s in registry["services"].items()
            if s["status"] == "scaffold" and (only is None or n == only)}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # console Windows mặc định cp1252, không in được tiếng Việt
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="chỉ báo file còn thiếu")
    ap.add_argument("--service", help="chỉ một service")
    args = ap.parse_args(argv)

    registry = load_registry()
    targets = scaffold_targets(registry, args.service)
    if not targets:
        print("Không có service nào cần dựng.")
        return 0

    missing, created = [], []
    for name, svc in targets.items():
        for rel, content in planned_files(name, svc).items():
            path = ROOT / rel
            if path.exists():
                continue
            missing.append(rel)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
                created.append(rel)

    if args.check:
        for rel in missing:
            print("THIẾU:", rel)
        return 1 if missing else 0
    for rel in created:
        print("tạo:", rel)
    print(f"Hoàn tất: {len(created)} file mới, các file có sẵn được giữ nguyên.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
