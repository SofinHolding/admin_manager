"""Test chassis dùng chung: health, request-id, envelope lỗi, log JSON."""

from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient
from manager_contracts import ApiError, ErrorCode
from manager_observability import configure_logging, create_service_app, current_request_id
from pydantic import BaseModel


class Login(BaseModel):  # cấp module: FastAPI cần phân giải kiểu khi dùng postponed annotations
    username: str
    password: str


def _app(checks=None):
    app = create_service_app(service_name="demo-service", service_version="9.9.9",
                             readiness_checks=checks)

    @app.post("/login")
    async def login(body: Login):  # noqa: ARG001
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret-internal-detail")

    @app.get("/api-error")
    async def api_error():
        raise ApiError(ErrorCode.SNAPSHOT_INCOMPLETE, "thiếu bản ghi", status_code=409,
                       details={"missing": 3})

    @app.get("/rid")
    async def rid():
        return {"rid": current_request_id()}

    return app


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


# ── Health ──────────────────────────────────────────────────────────────────────

def test_live_always_ok():
    r = _client(_app()).get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "live", "service": "demo-service"}


def test_ready_ok_without_checks():
    r = _client(_app()).get("/health/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_ready_503_when_own_check_fails():
    async def own_db():
        return False
    r = _client(_app({"own_db": own_db})).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"] == {"own_db": False}
    # live KHÔNG phụ thuộc ready: tiến trình vẫn sống để orchestrator không giết oan
    assert _client(_app({"own_db": own_db})).get("/health/live").status_code == 200


def test_ready_503_when_check_raises_not_500():
    async def broken():
        raise ConnectionError("db down")
    r = _client(_app({"own_db": broken})).get("/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["own_db"] is False


def test_version_endpoint():
    r = _client(_app()).get("/health/version")
    assert r.json() == {"service": "demo-service", "version": "9.9.9",
                        "contract_version": "2026-10-01"}


# ── Request-id ──────────────────────────────────────────────────────────────────

def test_request_id_generated_when_absent():
    r = _client(_app()).get("/rid")
    rid = r.headers["X-Request-Id"]
    assert rid.startswith("req_") and r.json()["rid"] == rid


def test_valid_incoming_request_id_is_kept():
    r = _client(_app()).get("/rid", headers={"X-Request-Id": "trace-abc-12345"})
    assert r.headers["X-Request-Id"] == "trace-abc-12345"


@pytest.mark.parametrize("bad", ["x", "has space in it", "a" * 200, "inj<script>alert(1)</script>"])
def test_untrusted_request_id_is_replaced(bad):
    r = _client(_app()).get("/rid", headers={"X-Request-Id": bad})
    assert r.headers["X-Request-Id"] != bad and r.headers["X-Request-Id"].startswith("req_")


def test_request_id_not_leaked_across_requests():
    c = _client(_app())
    a = c.get("/rid", headers={"X-Request-Id": "first-request-1"}).json()["rid"]
    b = c.get("/rid").json()["rid"]
    assert a == "first-request-1" and b != a


# ── Envelope lỗi ────────────────────────────────────────────────────────────────

def test_404_uses_stable_envelope_with_matching_request_id():
    r = _client(_app()).get("/khong-ton-tai")
    body = r.json()
    assert r.status_code == 404
    assert body["ok"] is False and body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["request_id"] == r.headers["X-Request-Id"]


def test_validation_error_does_not_echo_input_values():
    r = _client(_app()).post("/login", json={"username": "alice", "password": 12345678})
    body = r.json()
    assert r.status_code == 422 and body["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert "12345678" not in r.text and "alice" not in r.text
    assert body["error"]["details"]["fields"][0]["loc"] == ["body", "password"]


def test_api_error_maps_code_status_details():
    r = _client(_app()).get("/api-error")
    body = r.json()
    assert r.status_code == 409
    assert body["error"] == {"code": "SNAPSHOT_INCOMPLETE", "message": "thiếu bản ghi",
                             "details": {"missing": 3}}


def test_unhandled_exception_is_500_without_internal_detail():
    r = _client(_app()).get("/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "SERVER_ERROR"
    assert "secret-internal-detail" not in r.text and "Traceback" not in r.text


# ── Log JSON ────────────────────────────────────────────────────────────────────

def test_logs_are_json_with_service_and_request_id():
    stream = io.StringIO()
    configure_logging("demo-service", "9.9.9", "INFO")
    handler = logging.getLogger().handlers[0]
    handler.setStream(stream)

    app = _app()

    @app.get("/log")
    async def log():
        logging.getLogger("demo").info("xin chào", extra={"client_id": "cli_1"})
        return {}

    _client(app).get("/log", headers={"X-Request-Id": "req-for-log-1"})
    line = [ln for ln in stream.getvalue().splitlines() if '"xin chào"' in ln][0]
    rec = json.loads(line)
    assert rec["service_name"] == "demo-service" and rec["service_version"] == "9.9.9"
    assert rec["request_id"] == "req-for-log-1" and rec["client_id"] == "cli_1"
    assert rec["level"] == "INFO"
