"""Giới hạn body + streaming: 413 ngay khi đọc stream (kể cả chunked), không nạp cả body vào RAM, slowloris thân request."""

from __future__ import annotations

import hashlib
import time
import tracemalloc

import httpx
from conftest import client_for, raw_http

MIB = 1024 * 1024


# ── 413 ─────────────────────────────────────────────────────────────────────────

async def test_content_length_over_limit_is_413_without_touching_upstream(make_gateway, ups):
    async with client_for(make_gateway(GATEWAY_MAX_BODY_BYTES="1024")) as c:
        r = await c.post("/v1/resources/import", content=b"x" * 2000)
    assert r.status_code == 413 and r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert r.json()["error"]["details"]["reason"] == "body_too_large"
    assert ups.all_calls() == []


async def test_default_limit_is_2_mib():
    from api_gateway.config import load_settings

    assert load_settings({}).max_body_bytes == 2 * MIB


async def test_exact_limit_passes_and_one_byte_over_fails(make_gateway, ups):
    async with client_for(make_gateway(GATEWAY_MAX_BODY_BYTES="1024")) as c:
        ok = await c.post("/v1/resources/import", content=b"x" * 1024)
        over = await c.post("/v1/resources/import", content=b"x" * 1025)
    assert ok.status_code == 200 and ok.json()["body_len"] == 1024
    assert over.status_code == 413


async def test_chunked_body_over_limit_is_413_in_process(make_gateway, ups):
    async def body():
        for _ in range(8):
            yield b"y" * 512  # không có Content-Length

    async with client_for(make_gateway(GATEWAY_MAX_BODY_BYTES="1024")) as c:
        r = await c.post("/v1/resources/import", content=body())
    assert r.status_code == 413
    assert all(call["body_len"] <= 1024 for call in ups.all_calls()), "upstream không được nhận quá giới hạn"
    assert not any(call["body_len"] == 4096 for call in ups.all_calls())


def test_chunked_413_is_sent_immediately_without_waiting_for_end_of_stream(make_gateway, serve, ups):
    """Gửi 1 chunk 4 KiB (giới hạn 1 KiB) rồi KHÔNG gửi chunk kết thúc: phải nhận 413 ngay."""
    port = serve(make_gateway(GATEWAY_MAX_BODY_BYTES="1024")).port
    head = (b"POST /v1/resources/import HTTP/1.1\r\nHost: g\r\nTransfer-Encoding: chunked\r\n"
            b"Content-Type: application/octet-stream\r\n\r\n")
    chunk = b"1000\r\n" + b"z" * 4096 + b"\r\n"
    started = time.perf_counter()
    status, headers, body = raw_http(port, head + chunk, read_timeout=3)
    assert status == 413, (status, body)
    assert time.perf_counter() - started < 2.5
    assert headers.get("connection") == "close"
    assert ups.all_calls() == [] or all(c["body_len"] <= 1024 for c in ups.all_calls())


async def test_chunked_over_real_tcp_is_413(make_gateway, serve, ups):
    port = serve(make_gateway(GATEWAY_MAX_BODY_BYTES="2048")).port

    async def body():
        for _ in range(100):
            yield b"q" * 1000

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
        try:
            r = await c.post("/v1/resources/import", content=body())
            assert r.status_code == 413
        except (httpx.WriteError, httpx.ReadError, httpx.RemoteProtocolError):
            pass  # server đóng kết nối khi client còn đang gửi (RST) — hành vi chuẩn khi cắt upload; 413 đã được test thô
    assert not [x for x in ups.all_calls() if x["body_len"] > 2048]



async def test_body_within_limit_arrives_intact_chunked_and_declared(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    data = b"".join(bytes([i % 251]) * 997 for i in range(300))

    async def gen():
        for i in range(0, len(data), 4096):
            yield data[i:i + 4096]

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
        a = await c.post("/v1/resources/import", content=data)
        b = await c.post("/v1/resources/import", content=gen())
    for r in (a, b):
        assert r.status_code == 200 and r.json()["body_sha"] == hashlib.sha256(data).hexdigest()
    assert b.json()["headers"].get("content-length") is None, "chunked ⇒ không bịa Content-Length"


# ── Streaming không nạp RAM ─────────────────────────────────────────────────────

async def test_large_request_body_is_streamed_not_buffered(make_gateway, serve, ups):
    port = serve(make_gateway(GATEWAY_MAX_BODY_BYTES=str(512 * MIB))).port
    total_mib = 48
    block = b"A" * MIB
    expected = hashlib.sha256(block * total_mib).hexdigest()

    async def gen():
        for _ in range(total_mib):
            yield block

    tracemalloc.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as c:
            r = await c.post("/v1/resources/import", content=gen())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    rec = r.json()
    assert r.status_code == 200 and rec["body_len"] == total_mib * MIB and rec["body_sha"] == expected
    assert rec["chunks"] > total_mib, "upstream phải thấy body theo nhiều chunk nhỏ"
    assert rec["max_chunk"] <= 4 * MIB
    # Gateway + upstream + client cùng một tiến trình; nếu gateway nạp cả 48 MiB thì peak >= 48 MiB.
    assert peak < 24 * MIB, f"peak RAM {peak / MIB:.1f} MiB — gateway có vẻ buffer body"


async def test_large_response_is_streamed_not_buffered(make_gateway, serve, ups):
    port = serve(make_gateway()).port
    got = 0
    tracemalloc.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as c:
            async with c.stream("GET", "/v1/resources/__big?mb=64") as r:
                assert r.status_code == 200 and r.headers["content-length"] == str(64 * MIB)
                async for chunk in r.aiter_raw():
                    got += len(chunk)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert got == 64 * MIB
    assert peak < 24 * MIB, f"peak RAM {peak / MIB:.1f} MiB — gateway có vẻ buffer response"


# ── Slowloris thân request ──────────────────────────────────────────────────────

def test_stalled_body_gets_408_not_a_hung_worker(make_gateway, serve, ups):
    port = serve(make_gateway(GATEWAY_BODY_TIMEOUT_S="0.4")).port
    head = b"POST /v1/resources/import HTTP/1.1\r\nHost: g\r\nContent-Length: 100\r\n\r\nabcde"
    started = time.perf_counter()
    status, headers, _ = raw_http(port, head, read_timeout=5)
    elapsed = time.perf_counter() - started
    assert status == 408 and elapsed < 3
    assert ups.all_calls() == []


def test_dripping_body_is_cut_off(make_gateway, serve, ups):
    """Gửi từng byte chậm hơn timeout giữa hai chunk ⇒ bị cắt, không thể giữ kết nối vô hạn."""
    port = serve(make_gateway(GATEWAY_BODY_TIMEOUT_S="0.3")).port
    head = b"POST /v1/resources/import HTTP/1.1\r\nHost: g\r\nContent-Length: 50\r\n\r\n"
    status, _, _ = raw_http(port, head, chunks=[b"a", b"b", b"c"], chunk_delay=0.6, read_timeout=5)
    assert status == 408


async def test_client_abort_mid_upload_does_not_break_gateway(make_gateway, serve, ups):
    import socket

    port = serve(make_gateway()).port
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(b"POST /v1/resources/import HTTP/1.1\r\nHost: g\r\nContent-Length: 100000\r\n\r\n" + b"x" * 500)
    s.close()
    time.sleep(0.3)
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
        assert (await c.get("/v1/viewer/summary")).status_code == 200
        assert (await c.get("/health/live")).status_code == 200
