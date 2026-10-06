"""Upstream chết/chậm/đứt: lỗi nhanh có envelope, route khác vẫn chạy, circuit breaker, health không phụ thuộc upstream."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from api_gateway.app import Gateway
from api_gateway.config import load_settings
from api_gateway.routing.breaker import CircuitBreaker
from api_gateway.routing.table import RouteTable
from conftest import client_for


async def test_dead_upstream_is_502_fast_and_other_routes_still_work(make_gateway, ups):
    # Windows loopback retry kết nối bị từ chối ~2s ⇒ ConnectTimeout (504); Linux ⇒ ConnectError (502). Cả hai hợp lệ.
    gw = make_gateway(dead=("identity",), GATEWAY_CONNECT_TIMEOUT_S="0.4")
    async with client_for(gw) as c:
        started = time.perf_counter()
        r = await c.post("/v1/auth/login", content=b"{}")
        elapsed = time.perf_counter() - started
        body = r.json()
        assert r.status_code in (502, 504) and body["ok"] is False and body["error"]["code"] == "UNAVAILABLE"
        assert body["meta"]["request_id"] == r.headers["x-request-id"]
        assert elapsed < 1.5, f"upstream chết phải lỗi nhanh, mất {elapsed:.2f}s"
        assert "127.0.0.1" not in r.text and "Traceback" not in r.text, "không lộ địa chỉ upstream/traceback"
        # route của service khác không bị ảnh hưởng
        assert (await c.get("/v1/viewer/summary")).status_code == 200
        assert (await c.get("/v1/resources/x")).status_code == 200
        assert (await c.get("/v1/reward/jobs")).status_code == 200
        assert (await c.get("/health/live")).status_code == 200


async def test_slow_upstream_is_504_within_read_timeout(make_gateway, ups):
    gw = make_gateway(GATEWAY_READ_TIMEOUT_S="0.4")
    async with client_for(gw) as c:
        started = time.perf_counter()
        r = await c.get("/v1/resources/__slow?s=5")
        elapsed = time.perf_counter() - started
        assert r.status_code == 504 and r.json()["error"]["code"] == "UNAVAILABLE"
        assert elapsed < 2.0
        assert (await c.get("/v1/viewer/summary")).status_code == 200


async def test_slow_upstream_does_not_block_other_requests(make_gateway, ups):
    gw = make_gateway()
    async with client_for(gw) as c:
        slow = asyncio.create_task(c.get("/v1/resources/__slow?s=1.5"))
        await asyncio.sleep(0.1)
        started = time.perf_counter()
        fast = await c.get("/v1/viewer/summary")
        assert fast.status_code == 200 and time.perf_counter() - started < 1.0
        assert (await slow).status_code == 200


async def test_upstream_dying_mid_response_closes_connection_instead_of_truncating_silently(
        make_gateway, serve, ups):
    port = serve(make_gateway()).port
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
        with pytest.raises(httpx.HTTPError):  # RemoteProtocolError/ReadError: client biết phản hồi bị cụt
            await c.get("/v1/resources/__abort")
        assert (await c.get("/v1/viewer/summary")).status_code == 200  # gateway vẫn sống


# ── Circuit breaker ─────────────────────────────────────────────────────────────

async def test_breaker_opens_after_n_failures_and_stops_calling_upstream(make_gateway, ups):
    gw = make_gateway(GATEWAY_BREAKER_THRESHOLD="3", GATEWAY_BREAKER_COOLDOWN_S="0.6")
    echo = ups.echo["integration"]
    async with client_for(gw) as c:
        for _ in range(3):
            assert (await c.get("/v1/resources/__status?code=502")).status_code == 502
        assert len(echo.calls) == 3 and gw.proxy.breakers["integration"].state == "open"

        r = await c.get("/v1/resources/__status?code=200")
        assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"
        assert int(r.headers["retry-after"]) >= 1
        assert len(echo.calls) == 3, "mạch mở ⇒ KHÔNG gọi upstream"
        # upstream khác không bị ảnh hưởng
        assert (await c.get("/v1/viewer/summary")).status_code == 200

        await asyncio.sleep(0.7)  # hết cooldown ⇒ half-open: cho 1 request thăm dò
        probe = await c.get("/v1/resources/__status?code=200")
        assert probe.status_code == 200 and len(echo.calls) == 4
        assert gw.proxy.breakers["integration"].state == "closed"
        assert (await c.get("/v1/resources/x")).status_code == 200


async def test_failed_probe_reopens_the_breaker(make_gateway, ups):
    gw = make_gateway(GATEWAY_BREAKER_THRESHOLD="2", GATEWAY_BREAKER_COOLDOWN_S="0.4")
    echo = ups.echo["integration"]
    async with client_for(gw) as c:
        for _ in range(2):
            await c.get("/v1/resources/__status?code=504")
        await asyncio.sleep(0.5)
        assert (await c.get("/v1/resources/__status?code=502")).status_code == 502  # probe thất bại
        n = len(echo.calls)
        assert (await c.get("/v1/resources/x")).status_code == 503
        assert len(echo.calls) == n


async def test_dead_upstream_trips_breaker_then_fails_instantly(make_gateway, ups):
    gw = make_gateway(dead=("identity",), GATEWAY_BREAKER_THRESHOLD="3", GATEWAY_BREAKER_COOLDOWN_S="30",
                      GATEWAY_CONNECT_TIMEOUT_S="0.3")
    async with client_for(gw) as c:
        codes = [(await c.get("/v1/auth/me")).status_code for _ in range(3)]
        assert all(code in (502, 504) for code in codes), codes
        started = time.perf_counter()
        r = await c.get("/v1/auth/me")
        assert r.status_code == 503 and time.perf_counter() - started < 0.2


async def test_ordinary_4xx_5xx_do_not_trip_breaker(make_gateway, ups):
    gw = make_gateway(GATEWAY_BREAKER_THRESHOLD="2")
    async with client_for(gw) as c:
        for code in (400, 401, 404, 409, 500, 503, 500, 503):
            assert (await c.get(f"/v1/resources/__status?code={code}")).status_code == code
    assert gw.proxy.breakers["integration"].state == "closed"


def test_breaker_state_machine_with_fake_clock():
    now = [0.0]
    b = CircuitBreaker(threshold=3, cooldown_s=10, clock=lambda: now[0])
    assert b.allow() and b.state == "closed"
    b.record_failure(); b.record_failure()
    b.record_success()  # lỗi phải LIÊN TIẾP: thành công làm reset bộ đếm
    b.record_failure(); b.record_failure()
    assert b.state == "closed" and b.allow()
    b.record_failure()
    assert b.state == "open" and not b.allow() and b.retry_after() >= 1
    now[0] = 9.9
    assert not b.allow()
    now[0] = 10.1
    assert b.state == "half_open" and b.allow()
    assert not b.allow(), "half-open chỉ cho đúng một request thăm dò"
    b.record_failure()
    assert b.state == "open" and not b.allow()
    now[0] = 21
    assert b.allow()
    b.record_success()
    assert b.state == "closed" and b.allow() and b.allow()


def test_breaker_probe_that_never_reports_does_not_stick_forever():
    now = [0.0]
    b = CircuitBreaker(threshold=1, cooldown_s=5, clock=lambda: now[0])
    b.record_failure()
    now[0] = 6
    assert b.allow() and not b.allow()
    now[0] = 12  # probe cũ (client bỏ giữa chừng) quá hạn ⇒ cho thăm dò mới
    assert b.allow()


# ── Health độc lập với upstream ─────────────────────────────────────────────────

async def test_health_does_not_depend_on_upstreams(make_gateway, ups):
    gw = make_gateway(dead=("identity", "integration", "analytics", "reward"))
    async with client_for(gw) as c:
        live = await c.get("/health/live")
        ready = await c.get("/health/ready")
        ver = await c.get("/health/version")
    assert live.status_code == 200 and ready.status_code == 200 and ver.status_code == 200
    assert ready.json()["checks"] == {"config": True}
    for r in (live, ready, ver):
        assert "127.0.0.1" not in r.text and "http://" not in r.text, "health không được lộ cấu hình upstream"


async def test_health_requests_never_hit_upstream(make_gateway, ups):
    async with client_for(make_gateway()) as c:
        for p in ("/health/live", "/health/ready", "/health/version", "/openapi.json", "/v1/health"):
            assert (await c.get(p)).status_code == 200
    assert ups.all_calls() == []


async def test_ready_is_503_only_when_gateway_config_is_invalid(ups):
    settings = load_settings({"GATEWAY_RATE_LIMIT_ENABLED": "false"})
    broken = Gateway(settings, RouteTable([], {}))
    async with client_for(broken) as c:
        r = await c.get("/health/ready")
    assert r.status_code == 503 and r.json()["checks"] == {"config": False}


async def test_upstreams_health_is_local_only(make_gateway, ups):
    gw = make_gateway(dead=("identity",))
    async with client_for(gw, ip="127.0.0.1") as c:
        r = await c.get("/health/upstreams")
        body = r.json()
        assert r.status_code == 200 and body["status"] == "degraded"
        assert body["upstreams"]["identity"]["reachable"] is False
        assert body["upstreams"]["analytics"] == {"reachable": True, "circuit": "closed"}
        # đi qua proxy (có header chuyển tiếp) ⇒ coi như từ ngoài ⇒ 404
        fwd = await c.get("/health/upstreams", headers={"X-Forwarded-For": "8.8.8.8"})
        assert fwd.status_code == 404
        real = await c.get("/health/upstreams", headers={"X-Real-IP": "8.8.8.8"})
        assert real.status_code == 404
    async with client_for(gw, ip="203.0.113.9") as c:
        assert (await c.get("/health/upstreams")).status_code == 404


async def test_upstreams_health_does_not_leak_urls(make_gateway, ups):
    async with client_for(make_gateway(), ip="127.0.0.1") as c:
        r = await c.get("/health/upstreams")
    assert "http" not in r.text and ":8" not in r.text
    assert ups.all_calls() == [] or all(x["path"] == "/health/live" for x in ups.all_calls())


async def test_slow_admin_route_read_timeout_does_not_open_breaker(make_gateway, ups):
    """Rebuild/thao tác admin chậm ⇒ 504 nhưng KHÔNG đếm vào breaker (không làm mở mạch cả service)."""
    gw = make_gateway(GATEWAY_READ_TIMEOUT_S="0.2", GATEWAY_BREAKER_THRESHOLD="2", GATEWAY_BREAKER_COOLDOWN_S="30")
    async with client_for(gw) as c:
        for _ in range(4):
            assert (await c.get("/v1/admin/keys/__slow?s=2")).status_code == 504
        assert gw.proxy.breakers["identity"].state == "closed"
        assert (await c.post("/v1/auth/login", content=b"{}")).status_code != 503  # route thường của cùng service


async def test_slow_non_admin_route_read_timeout_still_counts_toward_breaker(make_gateway, ups):
    gw = make_gateway(GATEWAY_READ_TIMEOUT_S="0.2", GATEWAY_BREAKER_THRESHOLD="2", GATEWAY_BREAKER_COOLDOWN_S="30")
    async with client_for(gw) as c:
        for _ in range(2):
            assert (await c.get("/v1/resources/__slow?s=2")).status_code == 504
        assert gw.proxy.breakers["integration"].state == "open"
