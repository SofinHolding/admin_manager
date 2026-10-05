"""Test hợp đồng generic: envelope và mã lỗi ổn định."""

from __future__ import annotations

import re
from pathlib import Path

from manager_contracts import ApiError, ErrorCode, error_envelope, ok_envelope

REPO = Path(__file__).resolve().parents[4]


def test_ok_envelope_shape():
    env = ok_envelope({"x": 1}, request_id="req_abc12345")
    assert env == {"ok": True, "data": {"x": 1},
                   "meta": {"request_id": "req_abc12345", "contract_version": "2026-10-01"}}


def test_error_envelope_shape_and_default_details():
    env = error_envelope("SCOPE_DENIED", "no", request_id="req_abc12345")
    assert env["ok"] is False
    assert env["error"] == {"code": "SCOPE_DENIED", "message": "no", "details": {}}
    assert env["meta"] == {"request_id": "req_abc12345"}
    assert "data" not in env


def test_error_codes_are_stable_strings():
    # Giá trị phải trùng tên: client rẽ nhánh theo chuỗi, đổi chuỗi là breaking change.
    for code in ErrorCode:
        assert code.value == code.name


def test_api_error_defaults():
    err = ApiError(ErrorCode.SNAPSHOT_INCOMPLETE, "thiếu bản ghi")
    assert err.status_code == 400 and err.details == {} and err.code is ErrorCode.SNAPSHOT_INCOMPLETE


def test_every_code_in_ingest_contract_doc_exists():
    """Mọi mã lỗi liệt kê ở mục 6 của tài liệu hợp đồng ingest phải có trong ErrorCode."""
    doc = (REPO / "docs" / "api" / "ingest-data-contract-v1.md").read_text(encoding="utf-8")
    section = doc.split("## 6. Mã lỗi", 1)[1].split("## 7.", 1)[0]
    documented = set(re.findall(r"^\| `([A-Z][A-Z_]+)` \|", section, flags=re.M))
    assert documented, "không đọc được bảng mã lỗi (tài liệu đổi định dạng?)"
    missing = documented - {c.value for c in ErrorCode}
    assert not missing, f"mã lỗi có trong tài liệu nhưng thiếu trong ErrorCode: {sorted(missing)}"
