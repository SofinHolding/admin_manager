"""Che query nhạy cảm trong access log của uvicorn."""

from __future__ import annotations

import logging

from identity_service.logredact import RedactAccessLog, redact


def test_sensitive_query_values_are_redacted():
    out = redact("/oauth/logout?id_token_hint=eyJabc.def.ghi&state=x&post_logout_redirect_uri=https://a/b")
    assert "eyJabc" not in out and "id_token_hint=REDACTED" in out and "state=x" in out
    assert redact("/cb?code=abc123&state=s") == "/cb?code=REDACTED&state=s"
    assert redact("/v1/auth/verify-email?token=T0KEN") == "/v1/auth/verify-email?token=REDACTED"
    assert redact("/x?a=1&Access_Token=zzz") == "/x?a=1&Access_Token=REDACTED"
    assert redact("/health/live") == "/health/live"
    assert redact("/q?barcode=1") == "/q?barcode=1"          # chỉ khớp đúng tên tham số, không khớp hậu tố


def test_filter_rewrites_uvicorn_access_record_args():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                               '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:1", "GET", "/oauth/logout?id_token_hint=SECRET.JWT.VALUE", "1.1", 200),
                               None)
    assert RedactAccessLog().filter(record)
    assert "SECRET" not in record.getMessage() and "id_token_hint=REDACTED" in record.getMessage()
