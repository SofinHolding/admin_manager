"""Đối chiếu model pydantic của service với JSON Schema hợp đồng ingest (packages/contracts/ingest/2026-10-01).

Tính chất cần giữ: service KHÔNG BAO GIỜ chấp nhận thứ mà schema hợp đồng từ chối (nhất là `owner_*`), và chấp nhận
MỌI ví dụ hợp lệ của hợp đồng. Service được phép chặt hơn schema ở các ca đã liệt kê rõ ràng (ngày không tồn tại, số thực
không nguyên...).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema", reason="cần jsonschema (PYTHONPATH tới thư viện test); SKIP không phải PASS")

from integration_service.ingest import schemas  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "packages" / "contracts" / "ingest" / "2026-10-01"
SCHEMA = json.loads((BASE / "ingest.schema.json").read_text(encoding="utf-8"))
MODELS = {"ResourceBatch": schemas.ResourceBatch, "ContentBatch": schemas.ContentBatch, "MetricsBatch": schemas.MetricsBatch,
          "EventBatch": schemas.EventBatch, "SnapshotCreate": schemas.SnapshotCreate, "CommitRequest": schemas.CommitRequest}
# Ca service chặt hơn schema có CHỦ ĐÍCH (schema chỉ kiểm pattern/kiểu, không kiểm lịch hay bool-vs-int).
STRICTER_OK = {"date-not-in-calendar"}


def js_valid(definition: str, body) -> bool:
    sub = {"$ref": f"#/$defs/{definition}", "$defs": SCHEMA["$defs"]}
    return not list(jsonschema.Draft202012Validator(sub, format_checker=jsonschema.FormatChecker()).iter_errors(body))


def py_valid(definition: str, body) -> bool:
    try:
        MODELS[definition].model_validate(body)
        return True
    except Exception:  # noqa: BLE001 — ValidationError hoặc lỗi dựng model
        return False


def examples() -> list[tuple[str, str, dict]]:
    out = []
    for path in sorted((BASE / "examples").glob("*.json")):
        for name, body in json.loads(path.read_text(encoding="utf-8")).items():
            if not name.startswith("_") and name in MODELS:
                out.append((path.name, name, body))
    return out


@pytest.mark.parametrize("fname,name,body", examples(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_contract_example_is_accepted_by_the_service_models(fname, name, body):
    assert js_valid(name, body)
    assert py_valid(name, body), f"{fname}:{name} hợp lệ theo hợp đồng nhưng service từ chối"


def stricter_ok(op) -> bool:
    """Các ca service CHẶT HƠN schema có chủ đích: ngày không có trong lịch, bool thay số, timestamp sai định dạng
    (jsonschema chỉ kiểm `format: date-time` khi có thư viện RFC3339; service luôn kiểm và bắt buộc có múi giờ)."""
    value = op[2] if len(op) > 2 else None
    return (value == "2026-02-30" or isinstance(value, bool)
            or (isinstance(value, str) and str(op[1][-1]).endswith("_at")))


def mutations(body):
    """Sinh biến thể sai/lệch của một body hợp lệ: xoá từng khoá, đổi kiểu, thêm khoá lạ (đặc biệt owner_*)."""
    wrong = [None, 1, -1, 1.5, "", "x", True, [], {}, "2026-02-30", "05/10/2026"]

    def walk(node, path):
        if isinstance(node, dict):
            for k in list(node):
                yield ("del", path + [k])
                for w in wrong:
                    yield ("set", path + [k], w)
                yield from walk(node[k], path + [k])
            yield ("add", path + ["owner_account_id"], "acc_gia_mao")
            yield ("add", path + ["unexpected_field"], 1)
        elif isinstance(node, list) and node:
            yield from walk(node[0], path + [0])

    for op in walk(body, []):
        clone = copy.deepcopy(body)
        target = clone
        for p in op[1][:-1]:
            target = target[p]
        if op[0] == "del":
            del target[op[1][-1]]
        else:
            target[op[1][-1]] = op[2]
        yield op, clone


@pytest.mark.parametrize("fname,name,body", examples(), ids=lambda v: v if isinstance(v, str) else "")
def test_service_never_accepts_what_the_contract_schema_rejects(fname, name, body):
    checked = 0
    for op, mutated in mutations(body):
        js, py = js_valid(name, mutated), py_valid(name, mutated)
        assert not (py and not js), f"service chấp nhận thứ schema từ chối: {name} {op}"
        checked += 1
    assert checked > 5


@pytest.mark.parametrize("fname,name,body", examples(), ids=lambda v: v if isinstance(v, str) else "")
def test_service_accepts_every_schema_valid_mutation_except_documented_stricter_cases(fname, name, body):
    for op, mutated in mutations(body):
        if js_valid(name, mutated) and not py_valid(name, mutated):
            assert stricter_ok(op), f"service từ chối thứ hợp đồng cho phép: {name} {op}"


def test_owner_fields_are_rejected_at_every_level_of_every_model():
    for fname, name, body in examples():
        for op, mutated in mutations(body):
            if op[0] == "add" and op[1][-1] == "owner_account_id":
                assert not py_valid(name, mutated), (name, op)
                assert not js_valid(name, mutated), (name, op)


def test_commit_request_and_counts_edge_cases():
    for good in ({}, {"accept_partial": True}, {"accept_partial": False}):
        assert js_valid("CommitRequest", good) and py_valid("CommitRequest", good)
    for bad in ({"accept_partial": "true"}, {"accept_partial": 1}, {"owner_account_id": "x"}, {"x": 1}):
        assert not js_valid("CommitRequest", bad) and not py_valid("CommitRequest", bad)
    base = {"platform": "facebook", "snapshot_type": "delta", "observed_at": "2026-10-05T10:00:00Z",
            "report_timezone": "UTC", "expected_counts": {"resources": 0}}
    assert js_valid("SnapshotCreate", base) and py_valid("SnapshotCreate", base)
    for bad in ({**base, "expected_counts": {"resources": -1}}, {**base, "expected_counts": {"resources": "1"}},
                {**base, "expected_counts": {"rows": 1}}, {**base, "platform": "Facebook"}):
        assert not js_valid("SnapshotCreate", bad) and not py_valid("SnapshotCreate", bad)


def test_service_error_codes_for_ingest_are_the_documented_ones():
    from manager_contracts import ErrorCode
    for code in ("RECORD_VALIDATION_FAILED", "RESOURCE_UNKNOWN", "METRIC_UNKNOWN", "SNAPSHOT_SUPERSEDED",
                 "TIMEZONE_CHANGED", "BATCH_TOO_LARGE", "SNAPSHOT_INCOMPLETE", "SNAPSHOT_ALREADY_COMMITTED",
                 "IDEMPOTENCY_CONFLICT", "PLATFORM_NOT_ALLOWED", "SCOPE_DENIED", "AUTH_INVALID", "AUTH_REVOKED"):
        assert code in {c.value for c in ErrorCode}
