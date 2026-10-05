"""Mọi route legacy phải có đúng một service đích; quy tắc route không được che nhau."""

from __future__ import annotations

import json
from collections import Counter

import pytest
from archlib import ROOT, load_registry

REGISTRY = load_registry()
ROUTE_MAP = json.loads((ROOT / "infra" / "gateway" / "route-map.json").read_text(encoding="utf-8"))
RULES = ROUTE_MAP["rules"]
LEGACY_DIR = ROOT / "docs" / "api" / "legacy"
VALID_TARGETS = set(REGISTRY["services"]) | {"gateway"}


def resolve(path: str) -> str | None:
    for rule in RULES:  # thứ tự quy tắc là thứ tự ưu tiên
        prefix = rule["legacy_prefix"]
        if path == prefix or path.startswith(prefix + "/"):
            return rule["target"]
    return None


def legacy_operations() -> list[tuple[str, str, str]]:
    ops = []
    for file in sorted(LEGACY_DIR.glob("*.openapi.json")):
        spec = json.loads(file.read_text(encoding="utf-8"))
        for path, methods in spec["paths"].items():
            for method in methods:
                ops.append((file.name, method.upper(), path))
    ops.append(("manual", "GET", "/v1/health"))  # có trong main.py nhưng ẩn khỏi OpenAPI
    return ops


def test_legacy_baseline_was_exported():
    names = {p.name for p in LEGACY_DIR.glob("*.openapi.json")}
    assert names == {"admin-server.openapi.json", "reward-service.openapi.json"}
    assert len(legacy_operations()) >= 100  # 70 + 40 + health; đo được lúc xuất baseline


def test_every_legacy_route_has_a_target():
    unmapped = [f"{m} {p}" for _, m, p in legacy_operations() if resolve(p) is None]
    assert not unmapped, f"route legacy chưa có đích: {unmapped}"


def test_all_targets_are_known_services():
    for rule in RULES:
        assert rule["target"] in VALID_TARGETS, rule


def test_more_specific_rules_come_before_general_ones():
    """Quy tắc cụ thể (vd /v1/viewer/links) phải đứng trước quy tắc chung (/v1/viewer), nếu không bị che."""
    for i, general in enumerate(RULES):
        for specific in RULES[i + 1:]:
            gp, sp = general["legacy_prefix"], specific["legacy_prefix"]
            assert not sp.startswith(gp + "/"), f"`{sp}` bị che bởi `{gp}` đứng trước"


@pytest.mark.parametrize("path,expected", [
    ("/v1/auth/login", "identity-service"),
    ("/v1/admin/keys", "identity-service"),
    ("/v1/admin/users/{user_id}/status", "identity-service"),
    ("/v1/admin/cache/flush", "analytics-service"),
    ("/v1/admin/country-aliases/{alias}", "analytics-service"),
    ("/v1/viewer/summary", "analytics-service"),
    ("/v1/viewer/groups/{group_id}/members", "analytics-service"),
    ("/v1/viewer/links/code", "integration-service"),
    ("/v1/resources/import", "integration-service"),
    ("/v1/ops/batches", "integration-service"),
    ("/v1/reward/jobs", "reward-service"),
])
def test_key_routes_resolve_as_designed(path, expected):
    assert resolve(path) == expected


def test_prefix_match_requires_segment_boundary():
    assert resolve("/v1/authx/login") is None  # không khớp nhầm tiền tố chuỗi


def test_dashboard_routes_do_not_land_in_integration():
    """Dashboard thuộc Analytics; chỉ riêng pairing legacy (/viewer/links) thuộc Integration."""
    for _, _, path in legacy_operations():
        if path.startswith("/v1/viewer/") and not path.startswith("/v1/viewer/links"):
            assert resolve(path) == "analytics-service", path


def test_contract_negotiation_defaults_to_legacy():
    neg = ROUTE_MAP["contract_negotiation"]
    assert neg["default_when_absent"] == "legacy" and neg["new_contract"] == REGISTRY["contract_version"]


def test_report_distribution(capsys):
    counts = Counter(resolve(p) for _, _, p in legacy_operations())
    with capsys.disabled():
        print("\n  phân bổ route legacy:", dict(counts))
    assert None not in counts
