"""Cấu trúc bắt buộc của từng service (chassis) và tính toàn vẹn của registry."""

from __future__ import annotations

import re

import pytest
import scaffold_service
from archlib import (PER_SERVICE_COPIES, ROOT, created_tables, load_registry, module_header,
                     scaffold_services)

REGISTRY = load_registry()
SERVICES = scaffold_services(REGISTRY)
ALL = REGISTRY["services"]


# ── Registry ────────────────────────────────────────────────────────────────────

def test_registry_required_keys():
    required = {"status", "path", "package", "port", "database", "responsibility",
                "public_prefixes", "owned_tables", "emits", "consumes", "modules"}
    for name, svc in ALL.items():
        assert required <= set(svc), f"{name} thiếu {sorted(required - set(svc))}"


def test_ports_databases_packages_are_unique():
    for key in ("port", "database", "package"):
        values = [s[key] for s in ALL.values()]
        assert len(values) == len(set(values)), f"{key} bị trùng: {values}"


def test_each_service_has_its_own_database():
    for name, svc in ALL.items():
        assert svc["database"].endswith("_db"), name


def test_owned_tables_do_not_overlap_between_services():
    seen: dict[str, str] = {}
    for name, svc in ALL.items():
        for table in svc["owned_tables"]:
            if table in PER_SERVICE_COPIES:
                continue
            assert table not in seen, f"bảng `{table}` bị {seen[table]} và {name} cùng nhận sở hữu"
            seen[table] = name


def test_every_consumed_event_is_emitted_by_some_service():
    emitted = {e for s in ALL.values() for e in s["emits"]}
    for name, svc in ALL.items():
        for event in svc["consumes"]:
            family = event.split(".")[0]
            assert event in emitted or any(e.startswith(family + ".") for e in emitted), \
                f"{name} nhận `{event}` nhưng không service nào phát"


def test_public_prefixes_do_not_overlap():
    owner: dict[str, str] = {}
    for name, svc in ALL.items():
        for prefix in svc["public_prefixes"]:
            for other, other_owner in owner.items():
                assert not (prefix.startswith(other + "/") or other.startswith(prefix + "/")
                            or prefix == other), f"{prefix} ({name}) chồng {other} ({other_owner})"
            owner[prefix] = name


def test_phase_values_are_wellformed():
    for name, svc in ALL.items():
        for module, meta in svc["modules"].items():
            assert re.fullmatch(r"\d+(\.\d+)?|existing", meta["phase"]), f"{name}.{module}"
            assert meta["responsibility"].strip(), f"{name}.{module}"


# ── Chassis từng service ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(SERVICES))
def test_scaffold_is_complete(name):
    missing = [rel for rel in scaffold_service.planned_files(name, SERVICES[name])
               if not (ROOT / rel).exists()]
    assert not missing, f"thiếu file chassis: {missing} (chạy infra/scripts/scaffold_service.py)"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_module_declares_ownership_header(name):
    svc = SERVICES[name]
    pkg = ROOT / svc["path"] / "src" / svc["package"]
    for module, meta in svc["modules"].items():
        header = module_header(pkg / module / "__init__.py")
        assert header.get("module") == module, f"{name}.{module}: sai/thiếu `module:`"
        assert header.get("service") == name, f"{name}.{module}: sai/thiếu `service:`"
        assert header.get("phase") == meta["phase"], f"{name}.{module}: phase lệch registry"
        assert header.get("status") in {"planned", "active"}, f"{name}.{module}: status không hợp lệ"
        assert header.get("responsibility"), f"{name}.{module}: thiếu responsibility"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_no_undeclared_module_directories(name):
    """Module mới phải khai báo trong registry trước. Cấm thư mục module 'chui'."""
    svc = SERVICES[name]
    pkg = ROOT / svc["path"] / "src" / svc["package"]
    on_disk = {p.name for p in pkg.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    extra = on_disk - set(svc["modules"])
    assert not extra, f"{name} có module chưa khai báo trong registry: {sorted(extra)}"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_service_dockerfile_and_env_are_isolated(name):
    svc = SERVICES[name]
    docker = (ROOT / svc["path"] / "Dockerfile").read_text(encoding="utf-8")
    assert f"EXPOSE {svc['port']}" in docker and f"{svc['package']}.main:app" in docker
    # Image chỉ chứa code của chính service + package dùng chung; không COPY service khác.
    for other, o in ALL.items():
        if other != name:
            assert o["path"] not in docker, f"Dockerfile của {name} COPY {other}"
    env = (ROOT / svc["path"] / ".env.example").read_text(encoding="utf-8")
    assert svc["database"] in env
    for other, o in ALL.items():
        if other != name:
            assert o["database"] not in env, f".env của {name} trỏ DB của {other}"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_service_has_its_own_tests(name):
    tests = list((ROOT / SERVICES[name]["path"] / "tests").glob("test_*.py"))
    assert len(tests) >= 2


# ── Migration ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(SERVICES))
def test_migrations_only_create_owned_tables(name):
    svc = SERVICES[name]
    allowed = set(svc["owned_tables"]) | PER_SERVICE_COPIES
    for sql in sorted((ROOT / svc["path"] / "migrations").glob("*.sql")):
        assert re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", sql.name), f"tên migration sai: {sql.name}"
        extra = created_tables(sql.read_text(encoding="utf-8")) - allowed
        assert not extra, f"{sql.name} tạo bảng không thuộc {name}: {sorted(extra)}"


def test_migration_checker_detects_foreign_table(tmp_path):
    sql = "CREATE TABLE IF NOT EXISTS accounts (id text); CREATE TABLE resources (id text);"
    assert created_tables(sql) == {"accounts", "resources"}
