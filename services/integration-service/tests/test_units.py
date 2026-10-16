"""Test đơn vị thuần (không cần DB): API key, rate limiter, quy tắc hợp nhất, validate bản ghi, body limit, policy."""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from datetime import datetime, timezone

import pytest
from manager_contracts import ErrorCode

from integration_service import security
from integration_service.clients import policy
from integration_service.errors import ApiProblem
from integration_service.http_utils import BodyLimitMiddleware, EnvelopeResponse, idempotency_key, parse_json_object
from integration_service.ingest import merge, validate
from integration_service.ratelimit import FixedWindowLimiter


# ── API key ─────────────────────────────────────────────────────────────────────

def test_generated_keys_have_contract_format_and_are_unique():
    keys = [security.generate_api_key() for _ in range(200)]
    assert len({k[2] for k in keys}) == 200 and len({k[0] for k in keys}) == 200
    for key_id, secret, api_key in keys:
        assert re.fullmatch(r"[A-Za-z0-9_-]{12}", key_id) and re.fullmatch(r"[A-Za-z0-9_-]{43}", secret)
        assert api_key == f"mpk_{key_id}.{secret}" and security.parse_api_key(f"Bearer {api_key}") == (key_id, secret)


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer x", "Token mpk_" + "a" * 12 + "." + "b" * 43,
                                    "Bearer mpk_" + "a" * 11 + "." + "b" * 43, "Bearer mpk_" + "a" * 12 + "." + "b" * 42,
                                    "Bearer mpk_" + "a" * 12 + "." + "b" * 44, "Bearer mpk_" + "a" * 12 + "b" * 43,
                                    "Bearer mpk_" + "a!" * 6 + "." + "b" * 43, "Bearer eyJhbGciOiJSUzI1NiJ9.e30.sig"])
def test_malformed_api_keys_are_not_parsed(header):
    assert security.parse_api_key(header) is None


def test_verify_secret_is_hash_comparison_and_safe_without_a_record():
    _, secret, _ = security.generate_api_key()
    stored = security.sha256_hex(secret)
    assert security.verify_secret(secret, stored) is True
    assert security.verify_secret(secret + "x", stored) is False
    assert security.verify_secret(secret, None) is False                 # key_id không tồn tại: vẫn False, không lỗi
    assert security.verify_secret(secret, secret) is False               # hash ≠ plaintext
    assert re.fullmatch(r"[0-9a-f]{64}", stored) and secret not in stored


# ── Rate limiter ────────────────────────────────────────────────────────────────

def test_fixed_window_limiter_counts_per_key_and_resets():
    t = [0.0]
    lim = FixedWindowLimiter(3, clock=lambda: t[0])
    assert [lim.hit("a") for _ in range(5)] == [True, True, True, False, False]
    assert lim.hit("b") is True
    assert 1 <= lim.retry_after("a") <= 60
    with pytest.raises(ApiProblem) as e:
        lim.check("a")
    assert e.value.status_code == 429 and e.value.code == ErrorCode.RATE_LIMITED and "Retry-After" in e.value.headers
    t[0] = 59.9
    assert lim.hit("a") is False
    t[0] = 60.1
    assert lim.hit("a") is True
    assert lim.exceeded("zzz") is False


# ── Quy tắc hợp nhất ────────────────────────────────────────────────────────────

def dt(minute: int) -> datetime:
    return datetime(2026, 10, 5, 10, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize("row,obs,data,immutable,expected", [
    (None, dt(0), {"a": 1}, False, "created"),
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": False}, dt(6), {"a": 1}, False, "unchanged"),
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": False}, dt(5), {"a": 1}, False, "unchanged"),
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": False}, dt(6), {"a": 2}, False, "updated"),
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": False}, dt(5), {"a": 2}, False, "updated"),          # hoà: bản sau thắng
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": False}, dt(4), {"a": 2}, False, "stale"),
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": False}, dt(4), {"a": 1}, False, "stale"),
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": True}, dt(6), {"a": 1}, False, "updated"),          # hiện lại
    ({"observed_at": dt(5), "data": {"a": 1}, "removed": True}, dt(4), {"a": 1}, False, "stale"),
    ({"observed_at": dt(5), "data": {"v": None}, "removed": False}, dt(6), {"v": 0}, False, "updated"),      # null != 0
    ({"observed_at": dt(5), "data": {"v": 0}, "removed": False}, dt(6), {"v": None}, False, "updated"),
    ({"observed_at": dt(5), "data": {"v": 0}, "removed": False}, dt(6), {"v": 0}, False, "unchanged"),
    ({"observed_at": dt(5), "data": {"k": "a"}, "removed": False}, dt(9), {"k": "b"}, True, "stale"),        # bất biến
    ({"observed_at": dt(5), "data": {"k": "a"}, "removed": False}, dt(9), {"k": "a"}, True, "unchanged"),
    ({"observed_at": dt(5), "data": {"k": "a"}, "removed": True}, dt(9), {"k": "a"}, True, "updated"),
])
def test_merge_decision_table(row, obs, data, immutable, expected):
    assert merge.decide(row, obs, data, immutable=immutable) == expected


# ── Validate bản ghi ────────────────────────────────────────────────────────────

def item(**over):
    return {"external_id": "p1", "resource_external_id": "r1", "published_date": "2026-10-04",
            "metrics": {"views": 1}, "observed_at": "2026-10-05T10:00:00Z", **over}


def test_extract_items_request_level_rules():
    assert validate.extract_items({"items": [{}]}) == [{}]
    for bad in ({}, {"items": []}, {"items": "x"}, {"items": [1], "x": 1}):
        with pytest.raises(ApiProblem) as e:
            validate.extract_items(bad)
        assert e.value.code == ErrorCode.RECORD_VALIDATION_FAILED and e.value.status_code == 422
    with pytest.raises(ApiProblem) as e:
        validate.extract_items({"items": [{}] * 501})
    assert e.value.code == ErrorCode.BATCH_TOO_LARGE and e.value.status_code == 413
    for spoof in ({"items": [{"owner_account_id": "x"}]}, {"items": [{}], "Owner": "x"}, {"items": [{"ownerId": "x"}]}):
        with pytest.raises(ApiProblem) as e:
            validate.extract_items(spoof)
        assert e.value.code == ErrorCode.RECORD_VALIDATION_FAILED and "owner" in e.value.message
    assert len(validate.extract_items({"items": [{}] * 500})) == 500


def test_validate_items_dedupes_inside_a_batch_newer_observed_at_wins():
    older, newer = item(metrics={"views": 1}), item(metrics={"views": 9}, observed_at="2026-10-05T11:00:00Z")
    staged, rej, dupes = validate.validate_items("content", [newer, older])
    assert len(staged) == 1 and dupes == 1 and staged[0].payload["metrics"] == {"views": 9} and not rej
    staged, _, dupes = validate.validate_items("content", [item(metrics={"views": 1}), item(metrics={"views": 2})])
    assert staged[0].payload["metrics"] == {"views": 2}                    # hoà observed_at: bản sau thắng


def test_validate_items_normalizes_timestamps_to_utc_and_keeps_null():
    staged, rej, _ = validate.validate_items("content", [item(observed_at="2026-10-05T17:00:00+07:00",
                                                              metrics={"views": None, "reactions": 0})])
    assert staged[0].payload["observed_at"] == "2026-10-05T10:00:00Z" and staged[0].observed_at.tzinfo is not None
    assert staged[0].payload["metrics"] == {"views": None, "reactions": 0} and not rej


def test_validate_items_rejection_messages_do_not_echo_values():
    secret = "GIA_TRI_RIENG_TU"
    _, rej, _ = validate.validate_items("content", [item(metrics={"views": secret}), item(external_id=secret, bogus=secret)])
    # external_id là định danh bản ghi (hợp đồng yêu cầu trả về); MESSAGE thì không được phản chiếu giá trị
    assert len(rej) == 2 and all(secret not in r.message for r in rej)


def test_known_metrics_filter_gives_metric_unknown_with_hint():
    _, rej, _ = validate.validate_items("content", [item(metrics={"views": 1, "coin": 2})], known_metrics={"views"})
    assert rej[0].code == "METRIC_UNKNOWN" and "extensions" in rej[0].message and rej[0].item_key == '["r1", "p1"]'


def test_daily_metric_and_event_kinds_validate():
    s, r, _ = validate.validate_items("metrics", [{"resource_external_id": "r", "date": "2026-10-01", "metrics": {"views": 3},
                                                   "observed_at": "2026-10-05T10:00:00Z"}])
    assert len(s) == 1 and not r and s[0].item_key == '["r", "2026-10-01"]'
    s, r, _ = validate.validate_items("events", [{"external_id": "e", "resource_external_id": "r", "kind": "post_ok",
                                                  "occurred_date": "2026-10-01"}], default_observed_at=dt(0))
    assert len(s) == 1 and s[0].observed_at == dt(0)
    s, r, _ = validate.validate_items("events", [{"external_id": "e", "resource_external_id": "r", "kind": "Post OK",
                                                  "occurred_date": "2026-10-01"}], default_observed_at=dt(0))
    assert not s and r[0].code == "RECORD_VALIDATION_FAILED"


# ── Body / header ───────────────────────────────────────────────────────────────

def test_idempotency_key_parsing():
    class R:
        def __init__(self, v):
            self.headers = {} if v is None else {"idempotency-key": v}
    assert idempotency_key(R(None)) is None and idempotency_key(R("abcd1234-ef")) == "abcd1234-ef"
    for bad in ("short", "x" * 129, "has space123", "a\nb" * 5):
        with pytest.raises(ApiProblem):
            idempotency_key(R(bad))


def test_parse_json_object_rejects_non_objects_and_nan():
    assert parse_json_object(b'{"a": 1}') == {"a": 1}
    for bad in (b"[]", b'"x"', b"nope", b'{"a": NaN}', b'{"a": Infinity}', b"\xff\xfe", b""):
        with pytest.raises(ApiProblem):
            parse_json_object(bad)


def test_envelope_response_renders_dates_and_unicode():
    out = EnvelopeResponse(content={"t": datetime(2026, 10, 5, 10, tzinfo=timezone.utc), "s": "tiếng Việt"}).body
    assert json.loads(out) == {"t": "2026-10-05T10:00:00Z", "s": "tiếng Việt"} and "tiếng".encode() in out


async def run_asgi(max_bytes: int, body: bytes, headers: dict | None = None, chunk: int = 1024):
    seen = {}

    async def app(scope, receive, send):
        data = b""
        while True:
            msg = await receive()
            data += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        seen["body"], seen["headers"] = data, dict(scope["headers"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    parts = [body[i:i + chunk] for i in range(0, len(body), chunk)] or [b""]
    queue = [{"type": "http.request", "body": p, "more_body": i < len(parts) - 1} for i, p in enumerate(parts)]
    sent: list[dict] = []

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)

    h = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await BodyLimitMiddleware(app, max_bytes)({"type": "http", "method": "POST", "headers": h, "path": "/x"}, receive, send)
    return seen, sent


async def test_body_limit_middleware_unit_cases():
    seen, sent = await run_asgi(100, b"x" * 100)
    assert sent[0]["status"] == 200 and seen["body"] == b"x" * 100
    seen, sent = await run_asgi(100, b"x" * 101, chunk=7)
    assert sent[0]["status"] == 413 and not seen
    seen, sent = await run_asgi(100, b"x" * 50, {"Content-Length": "5000"})
    assert sent[0]["status"] == 413 and not seen                       # từ chối theo Content-Length, không đọc body
    seen, sent = await run_asgi(1000, gzip.compress(b"y" * 900), {"Content-Encoding": "gzip"})
    assert sent[0]["status"] == 200 and seen["body"] == b"y" * 900 and b"content-encoding" not in seen["headers"]
    seen, sent = await run_asgi(1000, gzip.compress(b"y" * 5000), {"Content-Encoding": "gzip"})
    assert sent[0]["status"] == 413
    seen, sent = await run_asgi(1000, gzip.compress(b"y" * 900)[:-4], {"Content-Encoding": "gzip"})
    assert sent[0]["status"] == 400                                    # gzip cụt
    seen, sent = await run_asgi(1000, b"x", {"Content-Encoding": "deflate"})
    assert sent[0]["status"] == 415
    body = json.loads(sent[1]["body"])
    assert body["ok"] is False and body["error"]["code"] == "UNSUPPORTED_ENCODING" and body["meta"]["request_id"]
    assert asyncio.get_event_loop().is_running()


# ── Policy scope ────────────────────────────────────────────────────────────────

def test_scope_policy_never_grants_system_scope_through_clamping():
    assert policy.default_scopes("primary_tool") == list(policy.BASE_SCOPES)
    assert set(policy.default_scopes("fallback_tool")) == set(policy.BASE_SCOPES) | set(policy.FALLBACK_SCOPES)
    assert policy.SYSTEM_SCOPE not in policy.default_scopes("fallback_tool")
    assert policy.clamp_scopes([policy.SYSTEM_SCOPE, "jobs:claim", "snapshots:write"], "primary_tool") == ["snapshots:write"]
    assert policy.clamp_scopes([policy.SYSTEM_SCOPE, "jobs:claim"], "fallback_tool") == ["jobs:claim"]
    assert policy.clamp_scopes(None, "service") == list(policy.BASE_SCOPES)
    assert policy.clamp_scopes([], "primary_tool") == []
