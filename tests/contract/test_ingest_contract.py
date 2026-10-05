"""Hợp đồng ingest: ví dụ phải hợp lệ, các ca sai phải bị từ chối (nhất là owner do Tool tự gửi)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "packages" / "contracts" / "ingest" / "2026-10-01"
SCHEMA = json.loads((BASE / "ingest.schema.json").read_text(encoding="utf-8"))
EXAMPLES = sorted((BASE / "examples").glob("*.json"))


def errors(definition: str, body) -> list:
    sub = {"$ref": f"#/$defs/{definition}", "$defs": SCHEMA["$defs"]}
    return list(Draft202012Validator(sub, format_checker=FormatChecker()).iter_errors(body))


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_examples_validate(example):
    data = json.loads(example.read_text(encoding="utf-8"))
    checked = 0
    for name, body in data.items():
        if name.startswith("_"):
            continue
        assert name in SCHEMA["$defs"], f"{example.name}: `{name}` không có trong schema"
        assert not errors(name, body), f"{example.name}:{name}: {errors(name, body)[0].message}"
        checked += 1
    assert checked >= 3


BASE_ITEM = {"external_id": "x", "resource_external_id": "r", "published_date": "2026-10-05",
             "metrics": {"views": 1}, "observed_at": "2026-10-05T00:00:00Z"}


@pytest.mark.parametrize("why,item", [
    ("ngày sai định dạng", {**BASE_ITEM, "published_date": "05/10/2026"}),
    ("views âm", {**BASE_ITEM, "metrics": {"views": -1}}),
    ("views không nguyên", {**BASE_ITEM, "metrics": {"views": 1.5}}),
    ("thiếu observed_at (không áp được quy tắc bản mới thắng)",
     {k: v for k, v in BASE_ITEM.items() if k != "observed_at"}),
    ("Tool tự gửi owner_account_id (ownership phải suy từ credential)",
     {**BASE_ITEM, "owner_account_id": "acc_hack"}),
    ("khóa metric sai định dạng", {**BASE_ITEM, "metrics": {"Views!": 1}}),
])
def test_invalid_content_items_are_rejected(why, item):
    assert errors("ContentBatch", {"items": [item]}), why


def test_null_metric_is_allowed_but_distinct_from_zero():
    """null = chưa thu thập, 0 = đã thu thập và bằng 0 (ngữ nghĩa của stats_core, không được ép)."""
    assert not errors("ContentBatch", {"items": [{**BASE_ITEM, "metrics": {"views": None}}]})
    assert not errors("ContentBatch", {"items": [{**BASE_ITEM, "metrics": {"views": 0}}]})


def test_batch_limits():
    assert errors("ContentBatch", {"items": []})  # rỗng
    assert errors("ContentBatch", {"items": [BASE_ITEM] * 501})  # quá 500
    assert not errors("ContentBatch", {"items": [BASE_ITEM] * 500})


def test_snapshot_requires_timezone_and_expected_counts():
    ok = {"platform": "facebook", "snapshot_type": "full", "observed_at": "2026-10-05T00:00:00Z",
          "report_timezone": "UTC", "expected_counts": {}}
    assert not errors("SnapshotCreate", ok)
    for key in ("report_timezone", "expected_counts", "observed_at"):
        assert errors("SnapshotCreate", {k: v for k, v in ok.items() if k != key}), key


def test_no_tool_specific_names_in_contract():
    """Contract không được hardcode tên Tool/project cụ thể."""
    text = json.dumps(SCHEMA).lower()
    for banned in ("itl", "sible", "upload-facebook"):
        # khớp nguyên từ: "title" chứa "itl" nhưng không phải tên project
        assert not re.search(rf"(?<![a-z0-9]){re.escape(banned)}(?![a-z0-9])", text), banned


def test_name_check_is_word_based_not_substring():
    sample = '{"title": "x"}'
    assert not re.search(r"(?<![a-z0-9])itl(?![a-z0-9])", sample)
    assert re.search(r"(?<![a-z0-9])itl(?![a-z0-9])", '{"project": "itl"}')
