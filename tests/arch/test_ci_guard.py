"""Cổng an toàn CI/CD: trong giai đoạn tái cấu trúc, push KHÔNG được tự deploy lên VPS.

Khi hoàn thiện (Phase 6) và người dùng đồng ý bật lại deploy, hãy sửa test này CÙNG commit bật lại workflow,
để việc bật deploy là một quyết định có chủ đích chứ không phải do vô ý.
"""

from __future__ import annotations

import pytest
import yaml
from archlib import ROOT

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
FORBIDDEN_TRIGGERS = {"push", "schedule", "workflow_run", "release"}


def triggers(path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = data.get(True, data.get("on"))  # PyYAML đọc khóa `on` thành True
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on or {})


def test_workflows_exist():
    assert WORKFLOWS, "không tìm thấy workflow (đường dẫn đổi?)"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_automatic_deploy_triggers(workflow):
    bad = triggers(workflow) & FORBIDDEN_TRIGGERS
    assert not bad, (f"{workflow.name} có trigger tự động {sorted(bad)}. "
                     "Đang trong giai đoạn tái cấu trúc: chỉ cho phép workflow_dispatch.")


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_remain_manually_runnable(workflow):
    assert "workflow_dispatch" in triggers(workflow)


def test_guard_detects_push_trigger(tmp_path):
    f = tmp_path / "w.yml"
    f.write_text("on:\n  push:\n    branches: [main]\njobs: {}\n", encoding="utf-8")
    assert triggers(f) & FORBIDDEN_TRIGGERS == {"push"}
