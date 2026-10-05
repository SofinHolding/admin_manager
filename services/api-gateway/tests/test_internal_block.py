"""`/internal/*` KHÔNG BAO GIỜ được proxy, mọi biến thể (hợp đồng service-auth-contract-v1 §5)."""

from __future__ import annotations

import pytest
from api_gateway.routing.pathnorm import BlockedPath, InvalidPath, normalize_path
from conftest import client_for, get, raw_http

# Mỗi mục: đường dẫn thô như client gửi. Tất cả phải ra 404 và không chạm upstream.
BLOCKED_404 = [
    "/internal",
    "/internal/",
    "/internal/v1/feed",
    "/internal/v1/feed/accounts?after=0",
    "//internal/v1/feed",
    "///internal",
    "/INTERNAL/v1/feed",
    "/Internal/v1/feed",
    "/%69nternal/v1/feed",
    "/%49NTERNAL/v1/feed",
    "/%2569nternal/v1/feed",             # mã hoá hai lớp
    "/%252569nternal/v1/feed",           # ba lớp
    "/%2Finternal/v1/feed",              # dấu / mã hoá ở đầu
    "/v1/../internal/v1/feed",
    "/v1/auth/../../internal/v1/feed",
    "/v1/%2e%2e/internal/v1/feed",
    "/v1/%2E%2E%2Finternal/v1/feed",     # `..` + `/` mã hoá
    "/v1/..%2finternal/v1/feed",
    "/v1/%252e%252e/internal/v1/feed",
    "/./internal/v1/feed",
    "/.//internal",
    "/internal;x=1/v1/feed",
    "/internal%3Bx=1",
    "/internal./v1/feed",
    "/internal%20/v1/feed",
    "/internal%2F",
    "/v1/auth/internal/v1/feed",         # segment `internal` ở bất kỳ vị trí nào
    "/v1/viewer/..;/internal",
    "/%EF%BD%89nternal/v1/feed",         # chữ i toàn chiều rộng (NFKC → internal)
    "/internal?token=abc",
    "/internal/v1/feed#frag",
]
# Biến thể mà chính HTTP server có thể từ chối trước (400): chỉ cần KHÔNG BAO GIỜ tới upstream.
BLOCKED_ANY_4XX = [
    "/\\internal/v1/feed",
    "/v1\\..\\internal",
    "/internal%00",
    "/%00/internal",
    "/in\tternal",
]


def test_there_are_at_least_fifteen_variants():
    assert len(BLOCKED_404) >= 15


@pytest.mark.parametrize("target", BLOCKED_404)
def test_internal_variants_are_404_and_never_reach_upstream(make_gateway, serve, ups, target):
    port = serve(make_gateway()).port
    status, headers, body = get(port, target)
    assert status == 404, f"{target!r} → {status}"
    assert b'"NOT_FOUND"' in body
    assert ups.all_calls() == [], f"{target!r} đã lọt tới upstream!"


@pytest.mark.parametrize("target", BLOCKED_ANY_4XX)
def test_malformed_internal_variants_never_reach_upstream(make_gateway, serve, ups, target):
    port = serve(make_gateway()).port
    status, _, _ = get(port, target)
    assert status in (400, 404), f"{target!r} → {status}"
    assert ups.all_calls() == []


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def test_internal_blocked_for_every_method(make_gateway, serve, ups, method):
    port = serve(make_gateway(GATEWAY_CORS_ORIGINS="https://app.example.com")).port
    extra = "Origin: https://app.example.com\r\nAccess-Control-Request-Method: GET\r\n" if method == "OPTIONS" else ""
    req = f"{method} /internal/v1/feed HTTP/1.1\r\nHost: gw\r\nConnection: close\r\nContent-Length: 0\r\n{extra}\r\n"
    status, _, _ = raw_http(port, req.encode())
    assert status == 404
    assert ups.all_calls() == []


def test_absolute_form_request_target_is_blocked(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    status, _, _ = raw_http(port, b"GET http://gw/internal/v1/feed HTTP/1.1\r\nHost: gw\r\nConnection: close\r\n\r\n")
    assert status in (400, 404)
    assert ups.all_calls() == []


def test_internal_with_service_key_header_is_still_blocked(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    status, _, _ = get(port, "/internal/v1/feed", "X-Service-Key: super-secret\r\n")
    assert status == 404 and ups.all_calls() == []


async def test_internal_blocked_in_process_too(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        for p in ("/internal", "/internal/v1/feed", "/INTERNAL", "/%69nternal", "//internal"):
            r = await c.get(p)
            assert r.status_code == 404, p
    assert ups.all_calls() == []


async def test_legit_paths_still_work_next_to_blocked_ones(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        assert (await c.get("/v1/auth/me")).status_code == 200
        assert (await c.get("/internal/v1/feed")).status_code == 404
        assert (await c.get("/v1/viewer/summary")).status_code == 200
    assert len(ups.all_calls()) == 2


# ── Đơn vị: bộ chuẩn hoá path ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [t.encode() for t in BLOCKED_404 if "?" not in t and "#" not in t])
def test_normalizer_blocks_internal(raw):
    with pytest.raises(BlockedPath):
        normalize_path(raw)


@pytest.mark.parametrize("raw,match", [
    (b"/v1/auth/login", "/v1/auth/login"),
    (b"/v1//auth///login", "/v1/auth/login"),
    (b"/v1/./auth/login", "/v1/auth/login"),
    (b"/v1/viewer/../auth/login", "/v1/auth/login"),
    (b"/v1/auth/login/", "/v1/auth/login/"),
    (b"/v1/%61uth/login", "/v1/auth/login"),
    (b"/v1/viewer/groups/x%20y", "/v1/viewer/groups/x y"),
    (b"/", "/"),
])
def test_normalizer_canonicalizes_legit_paths(raw, match):
    assert normalize_path(raw).match == match


@pytest.mark.parametrize("raw", [b"v1/auth", b"", b"*", b"/v1/%2fauth", b"/v1/%5Cauth", b"/v1/a\x00b",
                                 b"/v1/\xff", b"/v1/%ff", b"/v1/a b"])
def test_normalizer_rejects_malformed(raw):
    with pytest.raises(InvalidPath):
        normalize_path(raw)


def test_forwarded_path_is_safely_reencoded():
    n = normalize_path(b"/v1/viewer/groups/a%20b%25c")
    assert n.forward == b"/v1/viewer/groups/a%20b%25c"
    assert normalize_path(b"/v1/viewer/groups/%7Efoo").forward == b"/v1/viewer/groups/~foo"
