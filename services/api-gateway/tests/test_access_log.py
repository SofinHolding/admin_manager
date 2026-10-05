"""Request-id và access log: KHÔNG BAO GIỜ log Authorization/cookie/body/query nhạy cảm."""

from __future__ import annotations

import pytest
from api_gateway.access_log.redact import REDACTED, redact_path, redact_query
from conftest import client_for, get

SECRETS = {
    "bearer": "SECRETBEARER9f8e7d6c5b4a",
    "cookie": "SECRETCOOKIE1a2b3c4d5e",
    "q_token": "SECRETQTOKEN7788",
    "q_password": "SECRETQPASS5566",
    "q_code": "SECRETQCODE3344",
    "q_key": "SECRETQAPIKEY1122",
    "q_secret": "SECRETQSECRET9900",
    "body": "SECRETBODYPASSWORD4321",
    "svc": "SECRETSERVICEKEY0001",
    "jwt_path": "eyJSECRETJWTPATHhead.payloadpart.sigpart",
    "mpk_path": "mpk_SECRETMPKPATH.abcdef",
}


async def _hit_with_secrets(gw):
    async with client_for(gw) as c:
        h = {"Authorization": f"Bearer {SECRETS['bearer']}", "Cookie": f"sid={SECRETS['cookie']}",
             "X-Service-Key": SECRETS["svc"]}
        q = (f"token={SECRETS['q_token']}&password={SECRETS['q_password']}&code={SECRETS['q_code']}"
             f"&api_key={SECRETS['q_key']}&client_secret={SECRETS['q_secret']}&page=2&search=hello")
        body = ('{"password": "%s", "username": "u"}' % SECRETS["body"]).encode()
        await c.post(f"/v1/auth/login?{q}", content=body, headers=h)             # thành công
        await c.get(f"/v1/auth/verify-email?{q}", headers=h)
        await c.get(f"/v1/viewer/groups/{SECRETS['jwt_path']}/members?{q}", headers=h)
        await c.get(f"/v1/viewer/groups/{SECRETS['mpk_path']}", headers=h)
        await c.get(f"/internal/v1/feed?{q}", headers=h)                           # bị chặn
        await c.get(f"/khong-co?{q}", headers=h)                                   # 404
        await c.post(f"/v1/resources/import?{q}", content=b"x" * 5000, headers=h)  # 413 (limit nhỏ)
        await c.request("TRACE", f"/v1/auth/login?{q}", headers=h)                 # 405


def _assert_no_secret(text: str):
    leaked = {k: v for k, v in SECRETS.items() if v in text}
    assert not leaked, f"secret bị lộ trong log: {sorted(leaked)}"


async def test_logs_never_contain_authorization_cookie_body_or_sensitive_query(make_gateway, ups, logs):
    await _hit_with_secrets(make_gateway(GATEWAY_MAX_BODY_BYTES="1024"))
    assert logs.text, "phải có log để quét"
    _assert_no_secret(logs.text)
    for forbidden in ("authorization", "cookie", "bearer ", "x-service-key"):
        assert forbidden not in logs.text.lower(), forbidden


async def test_logs_never_leak_secrets_on_upstream_failure_paths(make_gateway, ups, logs):
    gw = make_gateway(dead=("identity",), GATEWAY_CONNECT_TIMEOUT_S="0.3")
    async with client_for(gw) as c:
        await c.post(f"/v1/auth/login?token={SECRETS['q_token']}", content=SECRETS["body"].encode(),
                     headers={"Authorization": f"Bearer {SECRETS['bearer']}"})
    _assert_no_secret(logs.text)
    assert "HTTP Request" not in logs.text, "log của httpx (kèm URL/query) phải bị tắt"


async def test_access_line_has_expected_fields_and_redacted_query(make_gateway, ups, logs):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/summary?token=abc123&page=2&q=hello", headers={"X-Request-Id": "trace-abc-12345"})
    line = next(x for x in logs.lines() if x.get("event") == "access" and x["path"] == "/v1/viewer/summary")
    assert line["method"] == "GET" and line["status"] == 200 and line["upstream"] == "analytics"
    assert line["request_id"] == "trace-abc-12345" == r.headers["x-request-id"]
    assert isinstance(line["latency_ms"], (int, float)) and line["latency_ms"] >= 0
    assert line["query"] == f"token={REDACTED}&page=2&q=hello"
    assert line["service_name"] == "api-gateway"
    assert "authorization" not in line and "headers" not in line and "body" not in line


async def test_health_is_logged_separately_at_debug_not_info(make_gateway, ups, logs):
    async with client_for(make_gateway()) as c:
        await c.get("/health/live")
        await c.get("/v1/viewer/summary")
    import logging
    health = [x for x in logs.lines() if x.get("kind") == "health"]
    proxy = [x for x in logs.lines() if x.get("kind") == "proxy"]
    assert health and all(x["level"] == "DEBUG" for x in health)
    assert proxy and all(x["level"] == "INFO" for x in proxy)
    assert logging.getLogger("gateway.access").name == "gateway.access"


async def test_5xx_is_logged_at_warning(make_gateway, ups, logs):
    async with client_for(make_gateway(dead=("identity",), GATEWAY_CONNECT_TIMEOUT_S="0.3")) as c:
        await c.get("/v1/auth/me")
    lines = [x for x in logs.lines() if x.get("event") == "access" and x["status"] >= 500]
    assert lines and lines[0]["level"] == "WARNING" and lines[0]["outcome"] in ("upstream_unreachable", "upstream_timeout")


# ── Request-id ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("good", ["abcd1234", "req_0123456789abcdef", "trace-id.v2_OK-123", "A" * 128])
async def test_valid_request_id_is_kept_end_to_end(make_gateway, ups, good):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/summary", headers={"X-Request-Id": good})
    assert r.headers["x-request-id"] == good
    assert r.json()["headers"]["x-request-id"] == good, "upstream nhận cùng request-id"


@pytest.mark.parametrize("bad", ["short", "has space in it 123", "a" * 129, "ok-id-123;DROP", "<script>12345678",
                                 "id\twith\ttab-123", "ünicode-id-1234".encode("latin-1"), "x/y/z/12345678"])
async def test_invalid_request_id_is_replaced(make_gateway, ups, bad):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/summary", headers={"X-Request-Id": bad})
    rid = r.headers["x-request-id"]
    assert rid != (bad.decode("latin-1") if isinstance(bad, bytes) else bad) and rid.startswith("req_") and len(rid) >= 12
    assert r.json()["headers"]["x-request-id"] == rid


async def test_request_id_generated_when_absent_and_unique(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        ids = {(await c.get("/v1/viewer/summary")).headers["x-request-id"] for _ in range(5)}
    assert len(ids) == 5 and all(i.startswith("req_") for i in ids)


def test_crlf_injection_in_request_id_is_not_reflected(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    _, headers, _ = get(port, "/v1/viewer/summary", "X-Request-Id: abcdefgh123\r\nSet-Cookie: pwned=1\r\n")
    assert "set-cookie" not in headers  # `Set-Cookie` là header request riêng, không được phản chiếu


# ── Che secret (đơn vị) ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["token", "access_token", "refresh_token", "code", "password", "new_password", "pwd",
                                 "api_key", "apikey", "key", "secret", "client_secret", "sig", "signature",
                                 "session", "sid", "jwt", "authorization", "credential", "otp", "state"])
def test_sensitive_query_keys_are_redacted(key):
    assert redact_query(f"{key}=topsecretvalue&ok=1") == f"{key}={REDACTED}&ok=1"


def test_secret_looking_values_are_redacted_even_under_innocent_keys():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln"
    assert redact_query(f"x={jwt}") == f"x={REDACTED}"
    assert redact_query("y=mpk_abc123.def456") == f"y={REDACTED}"
    assert redact_query("z=" + "a" * 50) == f"z={REDACTED}"
    assert redact_query("w=" + "0123456789abcdef" * 2) == f"w={REDACTED}"


def test_query_redaction_is_bounded_and_clean():
    out = redact_query("&".join(f"k{i}=v" for i in range(30)))
    assert out.count("&") <= 21 and out.endswith("...")
    assert redact_query("&".join(f"k{i}=v" for i in range(500))) == REDACTED  # quá nhiều tham số: không log gì
    assert "\n" not in redact_query("a=b%0Ainjected%0D%0Aline") and "\x00" not in redact_query("a=%00x")
    assert len(redact_query("note=" + "n" * 5000)) <= 80


def test_path_redaction():
    assert redact_path("/v1/viewer/groups/abc/members") == "/v1/viewer/groups/abc/members"
    assert redact_path("/v1/x/mpk_ID.SECRET/y") == f"/v1/x/{REDACTED}/y"
    assert redact_path("/v1/x/" + "A" * 60) == f"/v1/x/{REDACTED}"
    assert "\n" not in redact_path("/v1/x/a\nb")
