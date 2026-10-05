"""Bảo mật biên: header an toàn, CORS allowlist, chống giả mạo IP/header nội bộ, method/header hợp lệ, rate limit."""

from __future__ import annotations

import pytest
from api_gateway.config import ConfigError, load_settings
from api_gateway.edge_security.ratelimit import RateLimiter, classify
from conftest import client_for, get, raw_http

ORIGIN = "https://app.example.com"


# ── Security headers ────────────────────────────────────────────────────────────

SECURITY = {"x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "no-referrer"}


async def test_security_headers_on_proxied_error_health_and_404(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        responses = [await c.get("/v1/viewer/summary"), await c.get("/nope"), await c.get("/health/live"),
                     await c.get("/health/ready"), await c.get("/internal/x"), await c.get("/v1/health")]
    for r in responses:
        for k, v in SECURITY.items():
            assert r.headers[k] == v, (r.request.url, k)
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert "strict-transport-security" not in r.headers  # dev + http


async def test_auth_routes_are_no_store_and_override_upstream_cache(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/auth/__headers")  # upstream đặt `public, max-age=300`
        assert r.headers["cache-control"] == "no-store"
        for p in ("/v1/auth/me", "/v1/accounts/me", "/v1/admin/users/x", "/oauth/token"):
            assert (await c.get(p)).headers["cache-control"] == "no-store", p


async def test_public_cache_header_kept_for_non_auth_routes_like_jwks(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/resources/__headers")
    assert r.headers["cache-control"] == "public, max-age=300"


async def test_hsts_only_in_production_or_https(make_gateway, ups):
    async with client_for(make_gateway(GATEWAY_ENV="production")) as c:
        assert "max-age=" in (await c.get("/health/live")).headers["strict-transport-security"]
        assert "strict-transport-security" in (await c.get("/nope")).headers
    async with client_for(make_gateway(GATEWAY_TRUSTED_PROXIES="203.0.113.0/24")) as c:
        r = await c.get("/health/live", headers={"X-Forwarded-Proto": "https"})
        assert "strict-transport-security" in r.headers
    async with client_for(make_gateway()) as c:  # peer không tin cậy: X-Forwarded-Proto bị bỏ qua
        r = await c.get("/health/live", headers={"X-Forwarded-Proto": "https"})
        assert "strict-transport-security" not in r.headers


async def test_upstream_internals_do_not_leak_in_response(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/resources/__headers", headers={"Origin": ORIGIN})
    low = {k.lower(): v for k, v in r.headers.items()}
    assert "server" not in low and "x-powered-by" not in low and "keep-alive" not in low
    assert low["x-upstream-extra"] == "kept"
    assert low["x-request-id"] != "upstream-chosen-id"
    assert "access-control-allow-origin" not in low, "CORS của upstream phải bị loại; gateway là nguồn duy nhất"
    assert "access-control-allow-credentials" not in low


def test_real_server_does_not_expose_server_header_or_traceback(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    for target in ("/nope", "/v1/viewer/summary", "/health/live"):
        _, headers, body = get(port, target)
        assert "server" not in headers and b"Traceback" not in body


async def test_unexpected_error_is_500_envelope_without_details(make_gateway, ups, monkeypatch):
    gw = make_gateway()

    async def boom(*a, **k):
        raise RuntimeError("password=hunter2 /etc/secret")

    monkeypatch.setattr(gw, "_dispatch", boom)
    async with client_for(gw) as c:
        r = await c.get("/v1/viewer/summary")
    assert r.status_code == 500 and r.json()["error"]["code"] == "SERVER_ERROR"
    assert "hunter2" not in r.text and "Traceback" not in r.text and "/etc/secret" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"


# ── CORS ────────────────────────────────────────────────────────────────────────

async def test_cors_allowed_origin_gets_exact_origin_and_vary(make_gateway, ups):
    async with client_for(make_gateway(GATEWAY_CORS_ORIGINS=f"{ORIGIN}, https://admin.example.com")) as c:
        r = await c.get("/v1/viewer/summary", headers={"Origin": ORIGIN})
        assert r.headers["access-control-allow-origin"] == ORIGIN
        assert "Origin" in r.headers["vary"]
        assert "access-control-allow-credentials" not in r.headers
        assert "x-request-id" in r.headers["access-control-expose-headers"].lower()
        # cả phản hồi lỗi cũng có CORS để trình duyệt đọc được lỗi
        e = await c.get("/nope", headers={"Origin": "https://admin.example.com"})
        assert e.status_code == 404 and e.headers["access-control-allow-origin"] == "https://admin.example.com"


@pytest.mark.parametrize("origin", ["https://evil.com", "https://app.example.com.evil.com", "http://app.example.com",
                                    "https://app.example.com:8443", "null", "https://sub.app.example.com", "*",
                                    "https://app.example.com/"])
async def test_cors_unknown_origin_gets_no_cors_headers(make_gateway, ups, origin):
    async with client_for(make_gateway(GATEWAY_CORS_ORIGINS=ORIGIN)) as c:
        r = await c.get("/v1/viewer/summary", headers={"Origin": origin})
    assert r.status_code == 200
    assert not [k for k in r.headers if k.lower().startswith("access-control-")]


async def test_cors_disabled_when_no_allowlist(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/summary", headers={"Origin": ORIGIN})
        assert "access-control-allow-origin" not in r.headers
        p = await c.options("/v1/viewer/summary", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
        assert p.status_code == 403


async def test_cors_credentials_never_with_wildcard(make_gateway, ups):
    async with client_for(make_gateway(GATEWAY_CORS_ORIGINS=ORIGIN, GATEWAY_CORS_ALLOW_CREDENTIALS="true")) as c:
        r = await c.get("/v1/viewer/summary", headers={"Origin": ORIGIN})
        assert r.headers["access-control-allow-credentials"] == "true"
        assert r.headers["access-control-allow-origin"] == ORIGIN != "*"
        bad = await c.get("/v1/viewer/summary", headers={"Origin": "https://evil.com"})
        assert "access-control-allow-credentials" not in bad.headers


@pytest.mark.parametrize("value", ["*", "null", "https://*.example.com", "https://app.example.com/path",
                                   "ftp://app.example.com", "app.example.com", "https://a.com,*"])
def test_wildcard_and_malformed_cors_origins_fail_fast(value):
    with pytest.raises(ConfigError):
        load_settings({"GATEWAY_CORS_ORIGINS": value})


async def test_preflight_allowed(make_gateway, ups):
    async with client_for(make_gateway(GATEWAY_CORS_ORIGINS=ORIGIN)) as c:
        r = await c.options("/v1/auth/login", headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type, Idempotency-Key"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert r.headers["access-control-max-age"] == "600" and r.content == b""
    assert ups.all_calls() == [], "preflight do gateway trả lời, không tới upstream"


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.com", "Access-Control-Request-Method": "POST"},
    {"Origin": ORIGIN, "Access-Control-Request-Method": "TRACE"},
    {"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Evil-Header"},
])
async def test_preflight_denied_has_no_cors_headers(make_gateway, ups, headers):
    async with client_for(make_gateway(GATEWAY_CORS_ORIGINS=ORIGIN)) as c:
        r = await c.options("/v1/auth/login", headers=headers)
    assert r.status_code == 403
    assert not [k for k in r.headers if k.lower().startswith("access-control-")]
    assert ups.all_calls() == []


async def test_options_without_cors_headers_is_not_treated_as_preflight(make_gateway, ups):
    async with client_for(make_gateway(GATEWAY_CORS_ORIGINS=ORIGIN)) as c:
        await c.options("/v1/viewer/summary")
    assert len(ups.all_calls()) == 1


# ── Header client không được tin ────────────────────────────────────────────────

async def test_x_real_ip_cannot_be_spoofed(make_gateway, ups):
    async with client_for(make_gateway(), ip="203.0.113.9") as c:
        r = await c.get("/v1/viewer/summary", headers=[
            ("X-Real-IP", "1.2.3.4"), ("X-Real-IP", "5.5.5.5"), ("X-Forwarded-For", "6.6.6.6, 7.7.7.7"),
            ("Forwarded", "for=8.8.8.8"), ("X-Forwarded-Host", "evil.com"), ("X-Forwarded-Proto", "https"),
            ("X-Forwarded-Port", "1"), ("X-Forwarded-Server", "evil")])
    rec = r.json()
    pairs = rec["header_pairs"]
    assert [v for k, v in pairs if k == "x-real-ip"] == ["203.0.113.9"]
    assert [v for k, v in pairs if k == "x-forwarded-for"] == ["203.0.113.9"]
    assert [v for k, v in pairs if k == "x-forwarded-proto"] == ["http"]
    assert not [k for k, _ in pairs if k in ("forwarded", "x-forwarded-host", "x-forwarded-port",
                                            "x-forwarded-server")]


async def test_trusted_proxy_real_ip_is_honored_only_from_trusted_peer(make_gateway, ups):
    gw = make_gateway(GATEWAY_TRUSTED_PROXIES="203.0.113.0/24, ::1")
    async with client_for(gw, ip="203.0.113.5") as c:
        ok = await c.get("/v1/viewer/summary", headers={"X-Real-IP": "198.51.100.7"})
        junk = await c.get("/v1/viewer/summary", headers={"X-Real-IP": "not-an-ip; DROP"})
    async with client_for(gw, ip="192.0.2.50") as c:
        untrusted = await c.get("/v1/viewer/summary", headers={"X-Real-IP": "198.51.100.7"})
    assert ok.json()["headers"]["x-real-ip"] == "198.51.100.7"
    assert junk.json()["headers"]["x-real-ip"] == "203.0.113.5"
    assert untrusted.json()["headers"]["x-real-ip"] == "192.0.2.50"


async def test_internal_and_hop_by_hop_headers_from_client_are_stripped(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/summary", headers={
            "X-Service-Key": "stolen-key", "X-Internal-User": "admin", "X-Gateway-Auth": "1",
            "Proxy-Authorization": "Basic eDp5", "X-HTTP-Method-Override": "DELETE", "X-Original-URL": "/internal",
            "X-Rewrite-URL": "/internal", "Upgrade": "websocket", "TE": "trailers",
            "Connection": "keep-alive, X-Secret-Hop", "X-Secret-Hop": "1",
            "Cookie": "sid=abc", "Accept-Language": "vi"})
    h = r.json()["headers"]
    for gone in ("x-service-key", "x-internal-user", "x-gateway-auth", "proxy-authorization",
                 "x-http-method-override", "x-original-url", "x-rewrite-url", "upgrade", "te", "x-secret-hop"):
        assert gone not in h, gone
    assert h["cookie"] == "sid=abc" and h["accept-language"] == "vi"
    assert h["host"].startswith("127.0.0.1:"), "Host phải là của upstream"


def test_accept_encoding_is_never_silently_added(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    get(port, "/v1/viewer/summary")  # client thô không gửi Accept-Encoding
    get(port, "/v1/viewer/summary", "Accept-Encoding: br\r\n")
    seen = [c["headers"].get("accept-encoding") for c in ups.echo["analytics"].calls]
    assert seen == ["identity", "br"], "không để upstream nén khi client không yêu cầu; yêu cầu của client giữ nguyên"


# ── Method / kích thước / dạng request ──────────────────────────────────────────

@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "PROPFIND", "FOO"])
async def test_invalid_methods_are_405_envelope(make_gateway, ups, method):
    async with client_for(make_gateway()) as c:
        r = await c.request(method, "/v1/viewer/summary")
    assert r.status_code == 405 and r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in r.headers["allow"]
    assert ups.all_calls() == []


async def test_oversized_header_block_is_431(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/summary", headers={"X-Big": "a" * 20_000})
        assert r.status_code == 431
        many = {f"X-H{i}": "v" for i in range(150)}
        assert (await c.get("/v1/viewer/summary", headers=many)).status_code == 431
    assert ups.all_calls() == []


async def test_url_too_long_is_414(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        r = await c.get("/v1/viewer/summary?" + "a=b&" * 3000)
    assert r.status_code == 414 and ups.all_calls() == []


def test_huge_header_over_real_socket_is_rejected(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    status, _, _ = get(port, "/v1/viewer/summary", "X-Big: " + "a" * 200_000 + "\r\n")
    assert status in (400, 431) and ups.all_calls() == []


def test_conflicting_content_length_and_te_are_rejected(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    two_cl = b"POST /v1/auth/login HTTP/1.1\r\nHost: g\r\nContent-Length: 3\r\nContent-Length: 5\r\n" \
             b"Connection: close\r\n\r\nabc"
    cl_te = b"POST /v1/auth/login HTTP/1.1\r\nHost: g\r\nContent-Length: 3\r\nTransfer-Encoding: chunked\r\n" \
            b"Connection: close\r\n\r\n0\r\n\r\n"
    bad_cl = b"POST /v1/auth/login HTTP/1.1\r\nHost: g\r\nContent-Length: abc\r\nConnection: close\r\n\r\n"
    for req in (two_cl, cl_te, bad_cl):
        status, _, _ = raw_http(port, req)
        assert status == 400
    assert ups.all_calls() == []


async def test_websocket_scope_is_refused(make_gateway, ups):
    gw = make_gateway()
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(m):
        sent.append(m)

    await gw({"type": "websocket", "path": "/v1/viewer/ws", "headers": [], "scheme": "ws"}, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


# ── Rate limit ──────────────────────────────────────────────────────────────────

async def test_login_is_limited_tighter_with_retry_after(make_gateway, ups):
    gw = make_gateway(GATEWAY_RATE_LIMIT_ENABLED="true", GATEWAY_RATE_LIMIT_STRICT_PER_MIN="3")
    async with client_for(gw, ip="198.51.100.1") as c:
        codes = [(await c.post("/v1/auth/login", content=b"{}")).status_code for _ in range(5)]
        limited = await c.post("/v1/auth/login", content=b"{}")
        assert codes == [200, 200, 200, 429, 429]
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert int(limited.headers["retry-after"]) >= 1
        assert limited.headers["x-content-type-options"] == "nosniff"
        # nhóm khác không bị ảnh hưởng, health không bị giới hạn
        assert (await c.get("/v1/viewer/summary")).status_code == 200
        for _ in range(20):
            assert (await c.get("/health/live")).status_code == 200
    async with client_for(gw, ip="198.51.100.2") as c2:  # IP khác có hạn mức riêng
        assert (await c2.post("/v1/auth/login", content=b"{}")).status_code == 200
    assert sum(1 for x in ups.echo["identity"].calls if x["path"] == "/v1/auth/login") == 4


async def test_rate_limited_requests_do_not_reach_upstream(make_gateway, ups):
    gw = make_gateway(GATEWAY_RATE_LIMIT_ENABLED="true", GATEWAY_RATE_LIMIT_DEFAULT_PER_MIN="2")
    async with client_for(gw) as c:
        for _ in range(6):
            await c.get("/v1/analytics/x")
    assert len(ups.echo["analytics"].calls) == 2


def test_limiter_refills_with_time_and_groups_are_independent():
    now = [0.0]
    rl = RateLimiter(strict_per_min=6, auth_per_min=60, default_per_min=600, clock=lambda: now[0])
    assert all(rl.check("1.1.1.1", "/v1/auth/login") is None for _ in range(6))
    retry = rl.check("1.1.1.1", "/v1/auth/login")
    assert retry is not None and 1 <= retry <= 10  # 6/phút = 1 token / 10s
    assert rl.check("1.1.1.1", "/v1/auth/me") is None  # nhóm auth riêng
    now[0] += 10
    assert rl.check("1.1.1.1", "/v1/auth/login") is None
    assert rl.check("1.1.1.1", "/v1/auth/login") is not None


def test_limiter_disabled_and_classification():
    rl = RateLimiter(strict_per_min=1, auth_per_min=1, default_per_min=1, enabled=False)
    assert all(rl.check("x", "/v1/auth/login") is None for _ in range(50))
    assert classify("/v1/auth/login") == classify("/V1/AUTH/LOGIN/") == "strict"
    assert classify("/oauth/token") == "strict" and classify("/v1/reward/auth/login") == "strict"
    assert classify("/v1/auth/me") == "auth" and classify("/v1/auth/refresh") == "auth"
    assert classify("/v1/viewer/summary") == "default"


def test_limiter_memory_is_bounded(monkeypatch):
    import api_gateway.edge_security.ratelimit as rlmod

    monkeypatch.setattr(rlmod, "_MAX_KEYS", 100)
    now = [0.0]
    rl = RateLimiter(strict_per_min=1, auth_per_min=1, default_per_min=1, clock=lambda: now[0])
    for i in range(1000):
        now[0] += 0.001
        rl.check(f"10.0.{i // 250}.{i % 250}", "/v1/viewer/x")
    assert len(rl._buckets) <= 100
