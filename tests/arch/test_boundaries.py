"""Ranh giới service: cấm import chéo, cấm import module legacy, cấm tham chiếu bảng của service khác."""

from __future__ import annotations

import pytest
from archlib import (ROOT, foreign_table_refs, forbidden_imports, load_registry, python_files,
                     scaffold_services)

REGISTRY = load_registry()
SERVICES = scaffold_services(REGISTRY)


# ── Import ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(SERVICES))
def test_service_does_not_import_other_services_or_legacy(name):
    svc = SERVICES[name]
    violations = {}
    for path in python_files(ROOT / svc["path"] / "src", ROOT / svc["path"] / "tests"):
        bad = forbidden_imports(path.read_text(encoding="utf-8"), svc["package"], REGISTRY)
        if bad:
            violations[str(path.relative_to(ROOT))] = bad
    assert not violations, f"{name} import code ngoài ranh giới: {violations}"


@pytest.mark.parametrize("package_dir", ["packages/contracts/python", "packages/observability/src"])
def test_shared_packages_do_not_import_any_service(package_dir):
    """Package dùng chung chỉ được chứa tiện ích generic; import service là coupling ngược."""
    violations = {}
    for path in python_files(ROOT / package_dir):
        bad = forbidden_imports(path.read_text(encoding="utf-8"), None, REGISTRY)
        if bad:
            violations[str(path.relative_to(ROOT))] = bad
    assert not violations, violations


def test_checker_detects_cross_service_import():
    """Test của test: bộ kiểm tra phải bắt được vi phạm thật."""
    bad = "from integration_service.resources import repo\nimport os\n"
    assert forbidden_imports(bad, "identity_service", REGISTRY) == ["integration_service"]


def test_checker_detects_legacy_import():
    assert forbidden_imports("import stats_core\nfrom db import make_db\n", "analytics_service",
                             REGISTRY) == ["db", "stats_core"]


def test_checker_allows_own_package_and_shared_packages():
    ok = ("from identity_service.auth import login\nfrom manager_contracts import ApiError\n"
          "from manager_observability import create_service_app\nimport json\n")
    assert forbidden_imports(ok, "identity_service", REGISTRY) == []


def test_checker_ignores_relative_imports():
    assert forbidden_imports("from . import auth\nfrom .db import x\n", "identity_service",
                             REGISTRY) == []


def test_forbidden_legacy_list_covers_every_legacy_module():
    """Danh sách cấm phải đủ mọi module cấp cao của admin-server hiện có (không bỏ sót khi legacy đổi)."""
    tooling = {"create_admin", "import_local_data", "seed_qa_demo"}
    legacy = {p.stem for p in (ROOT / "admin-server").glob("*.py")} - tooling
    missing = legacy - set(REGISTRY["forbidden_legacy_modules"])
    assert not missing, f"thêm vào forbidden_legacy_modules: {sorted(missing)}"


# ── SQL / bảng của service khác ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(SERVICES))
def test_service_sql_never_touches_other_services_tables(name):
    svc = SERVICES[name]
    violations = {}
    for path in python_files(ROOT / svc["path"] / "src"):
        hits = foreign_table_refs(path.read_text(encoding="utf-8"), name, REGISTRY)
        if hits:
            violations[str(path.relative_to(ROOT))] = sorted(hits)
    assert not violations, violations


def test_detects_join_on_other_service_table():
    """Ca điển hình bị cấm: Reward/Analytics JOIN thẳng bảng accounts của Identity."""
    src = 'Q = "SELECT a.id FROM resources r JOIN accounts a ON a.id = r.owner_account_id"\n'
    assert foreign_table_refs(src, "integration-service", REGISTRY) == {"accounts"}


def test_detects_direct_update_of_other_service_table():
    src = 'Q = "UPDATE accounts SET role = $2 WHERE id = $1"\n'
    assert foreign_table_refs(src, "analytics-service", REGISTRY) == {"accounts"}


def test_own_tables_and_per_service_copies_are_allowed():
    src = 'Q = "INSERT INTO outbox (id) VALUES ($1); SELECT * FROM account_projection"\n'
    assert foreign_table_refs(src, "analytics-service", REGISTRY) == set()
    own = 'Q = "SELECT * FROM channel_groups"\n'
    assert foreign_table_refs(own, "analytics-service", REGISTRY) == set()


def test_non_sql_strings_are_ignored():
    assert foreign_table_refs('MSG = "chọn accounts để xem"\n', "analytics-service", REGISTRY) == set()
