"""CLAUDE.md là văn bản ràng buộc: không được biến mất hoặc mất các quy tắc cốt lõi."""

from __future__ import annotations

import pytest
from archlib import ROOT, load_registry

TEXT = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

REQUIRED_PHRASES = [
    "KHÔNG `git push`",
    "infra/service-registry.json",
    "python infra/scripts/test_all.py",
    "Không service nào import code của service khác",
    "Không đọc/ghi/JOIN bảng của service khác",
    "Role (user) và Scope (machine) là hai mô hình độc lập",
    "SKIP không phải là PASS",
    "Cache-Aside",
    "Không hardcode tên Tool",
    "observed_at",
]


@pytest.mark.parametrize("phrase", REQUIRED_PHRASES)
def test_core_rules_are_present(phrase):
    assert phrase in TEXT, f"CLAUDE.md thiếu quy tắc: {phrase}"


def test_every_service_is_mentioned_in_structure_or_registry_reference():
    # Danh sách service lấy từ registry; CLAUDE.md phải trỏ tới registry thay vì sao chép (tránh lệch).
    assert "services/<name>-service/" in TEXT
    assert len(load_registry()["services"]) >= 4


def test_referenced_paths_exist():
    for rel in ("docs/architecture/architecture-assessment-v2.md", "docs/api/ingest-data-contract-v1.md",
                "infra/scripts/scaffold_service.py", "infra/scripts/test_all.py",
                "infra/gateway/route-map.json", "tests/arch/test_ci_guard.py"):
        assert rel in TEXT and (ROOT / rel).exists(), rel
