"""Test logic thuần: state machine, phân loại lỗi, idempotency, parser — không chạm DB/mạng.

Đối chiếu hành vi với `test/reward/unit.test.js` (Node, đã pass 48/48)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from domain.error_classifier import classify
from domain.idempotency import item_key, next_nonce
from domain.item_state import assert_transition, can_transition
from domain.parser import normalize_username, parse_input, validate_rows


def test_item_state_transitions():
    assert can_transition("pending", "processing")
    assert not can_transition("processing", "pending")  # BỊ CẤM — mất write-ahead
    assert can_transition("processing", "success")
    assert can_transition("processing", "unknown")
    assert can_transition("retrying", "processing")
    assert can_transition("retrying", "failed")
    assert not can_transition("success", "failed")  # success hấp thụ, không có cạnh ra
    # unknown chỉ thoát được khi actor='operator'
    assert not can_transition("unknown", "success", actor="system")
    assert can_transition("unknown", "success", actor="operator")
    assert can_transition("unknown", "pending", actor="operator")
    with pytest.raises(ValueError):
        assert_transition("processing", "pending")


def test_error_classifier_http_status():
    assert classify(http_status=429) == classify(http_status=429)
    r = classify(http_status=429)
    assert (r.decision, r.failure_code, r.pause_job) == ("retry", "RATE_LIMITED", False)
    r = classify(http_status=400)
    assert (r.decision, r.failure_code, r.pause_job) == ("fail", "INVALID_COMMAND_TEXT", False)
    r = classify(http_status=401)
    assert (r.decision, r.failure_code, r.pause_job) == ("fail", "AUTH_INVALID", True)
    r = classify(http_status=403)
    assert (r.decision, r.failure_code, r.pause_job) == ("fail", "MISSING_PERMISSION", True)
    r = classify(http_status=404)
    assert (r.decision, r.failure_code, r.pause_job) == ("fail", "CHANNEL_NOT_FOUND", True)
    r = classify(http_status=500)
    assert (r.decision, r.failure_code, r.pause_job) == ("unknown", "UPSTREAM_5XX", False)
    r = classify(http_status=503)
    assert r.decision == "unknown" and r.failure_code == "UPSTREAM_5XX"


def test_error_classifier_httpx_exceptions_per_d4():
    # Chưa gửi được byte nào (I3: connect-refused) → retry.
    for exc in (httpx.ConnectError("boom"), httpx.ConnectTimeout("boom"), httpx.PoolTimeout("boom")):
        r = classify(exc=exc)
        assert (r.decision, r.failure_code) == ("retry", "NETWORK")

    # Đã bắt đầu gửi/chờ trả lời (I3: read timeout) → unknown, KHÔNG retry tự động.
    for exc in (httpx.ReadTimeout("boom"), httpx.WriteTimeout("boom"), httpx.WriteError("boom"),
                httpx.ReadError("boom"), httpx.RemoteProtocolError("boom")):
        r = classify(exc=exc)
        assert (r.decision, r.failure_code) == ("unknown", "SEND_RESULT_UNKNOWN")

    # Lỗi lạ không xác định được cũng rơi vào nhánh unknown (nhánh mặc định của Node).
    r = classify(exc=RuntimeError("weird"))
    assert (r.decision, r.failure_code) == ("unknown", "SEND_RESULT_UNKNOWN")


def test_idempotency_key_deterministic_and_nonce_unique_increasing():
    k1 = item_key(job_id=1, row_index=0, normalized_username="bob", point=10)
    k2 = item_key(job_id=1, row_index=0, normalized_username="bob", point=10)
    k3 = item_key(job_id=1, row_index=1, normalized_username="bob", point=10)
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 64  # sha256 hex

    nonces = [next_nonce() for _ in range(50)]
    assert len(set(nonces)) == len(nonces)
    assert all(int(nonces[i]) < int(nonces[i + 1]) for i in range(len(nonces) - 1))


def test_parser_normalize_username():
    assert normalize_username("@Bob") == "bob"
    assert normalize_username("<@!123456789012345>") == "123456789012345"
    assert normalize_username("  Alice  ") == "alice"


def test_parser_parse_input_and_validate():
    content = "\n".join([
        "# comment dòng này bị bỏ qua",
        "alice|10",
        "bob,20",
        "",
        "charlie|0",
        "dave|not-a-number",
        "alice|30",  # trùng username (dòng khác vẫn hợp lệ định dạng)
    ])
    rows = parse_input(content)
    assert [r.raw_username for r in rows] == ["alice", "bob", "charlie", "dave", "alice"]
    assert rows[0].point == 10 and rows[0].valid
    assert rows[1].point == 20 and rows[1].valid
    assert rows[2].point == 0 and not rows[2].valid  # point<=0 → invalid ngay ở bước parse
    assert rows[3].point is None and not rows[3].valid

    issues = validate_rows(rows)
    codes = {(i.row_index, i.code) for i in issues}
    assert (2, "INVALID_ROW") in codes
    assert (3, "INVALID_ROW") in codes
    assert (4, "DUPLICATE_USERNAME") in codes


def test_parser_empty_input_reports_issue():
    issues = validate_rows(parse_input("# chỉ có comment\n\n"))
    assert any(i.code == "EMPTY_INPUT" for i in issues)
