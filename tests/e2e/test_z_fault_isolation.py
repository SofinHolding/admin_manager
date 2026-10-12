"""Cô lập lỗi bằng tiến trình thật: một thành phần chết KHÔNG kéo các thành phần khác chết.

Mỗi test gây lỗi, kiểm các service còn lại làm việc đúng, rồi KHÔI PHỤC lỗi (dù test đỏ) để test sau chạy trên stack lành.
Chạy sau test_mock_tool_x (tên file `test_z_`): dùng nguồn dữ liệu riêng `f-ch-*` và không phụ thuộc thứ tự.
"""

from __future__ import annotations

import asyncio
import time

import asyncpg
import httpx
import pytest
from conftest import day, wait_for

import devenv

FAULT_CH = [{"external_id": "f-ch-1", "display_name": "Fault Channel", "country": {"code": "BR", "name": "Brazil"}}]


def post(n: int, views: int) -> dict:
    return {"external_id": f"f-p-{n}", "resource_external_id": "f-ch-1", "published_date": day(1), "metrics": {"views": views}}


@pytest.fixture(scope="module")
def seeded(world):
    """Dữ liệu nền: 100 views. Các bước sau gửi thêm bằng snapshot `delta` (không xoá cái đã có)."""
    world["tool"].handshake()
    world["tool"].push(resources=FAULT_CH, content=[post(0, 100)])
    return {"views": 100, "next": 1}


def total(api, who) -> int:
    r = api.http.get("/v1/analytics/summary", headers=who["headers"])
    assert r.status_code == 200, r.text
    return r.json()["total_views"]


def ingest_more(world, seeded, views: int = 10) -> int:
    """Tool đẩy thêm một bài qua gateway -> commit. Trả tổng views kỳ vọng."""
    n = seeded["next"]
    seeded["next"] += 1
    seeded["views"] += views
    out = world["tool"].push(snapshot_type="delta", content=[post(n, views)], resources=[])
    assert out["status"] == "committed"
    return seeded["views"]


def login_ok(api) -> None:
    r = api.http.post("/v1/auth/login", json={"username": "e2eowner", "password": __import__("conftest").PASSWORD})
    assert r.status_code == 200, r.text


def analytics_status(api, who, timeout: float = 15) -> int:
    started = time.time()
    try:
        r = httpx.get(api.stack.url("gateway", "/v1/analytics/summary"), headers=who["headers"], timeout=timeout)
        code = r.status_code
    except httpx.HTTPError:
        code = 599
    assert time.time() - started < timeout, "service chết phải thất bại NHANH, không treo"
    return code


@pytest.fixture
def guard(stack):
    """Bảo đảm mọi service đang chạy và không còn override lỗi sau mỗi test."""
    yield
    for name in ("identity", "integration", "analytics", "reward", "gateway"):
        stack.overrides.pop(name, None)
        if not stack.alive(name):
            stack.start(name)


def restart_with(stack, name: str, **env: str) -> None:
    stack.stop(name)
    stack.overrides.setdefault(name, {}).update(env)
    stack.start(name, wait=40)


def restore(stack, name: str) -> None:
    stack.stop(name)
    stack.overrides.pop(name, None)
    stack.start(name, wait=40)


# ── A. Dừng Analytics: Identity + Integration vẫn làm việc ─────────────────────────────────────────────────────

def test_stopping_analytics_keeps_login_and_ingest_working_and_it_catches_up_after_restart(stack, api, world, seeded, guard):
    owner = world["owner"]
    pids = {n: stack.pid(n) for n in ("identity", "integration", "reward", "gateway")}
    stack.stop("analytics")
    assert not stack.alive("analytics")
    login_ok(api)                                           # Identity: đăng nhập được
    expected = ingest_more(world, seeded)                   # Integration: ingest + commit được
    assert analytics_status(api, owner) in {502, 503, 504, 599}   # Dashboard: lỗi rõ ràng, nhanh
    assert stack.ready_status("identity") == 200 and stack.ready_status("integration") == 200
    assert {n: stack.pid(n) for n in pids} == pids and all(stack.alive(n) for n in pids)
    stack.start("analytics")                                # khởi động lại -> tự đuổi kịp từ feed
    wait_for(lambda: total(api, owner) == expected, timeout=40, what="Analytics đuổi kịp sau khi sống lại")


# ── B. Dừng Reward: Auth, Integration, Analytics vẫn làm việc ───────────────────────────────────────────────────

def test_stopping_reward_does_not_affect_auth_integration_or_analytics(stack, api, world, seeded, guard):
    owner = world["owner"]
    stack.stop("reward")
    login_ok(api)
    expected = ingest_more(world, seeded)
    wait_for(lambda: total(api, owner) == expected, timeout=30, what="Analytics vẫn cập nhật khi Reward chết")
    assert httpx.get(stack.url("gateway", "/v1/reward/health"), timeout=10).status_code in {502, 503, 504}
    assert stack.ready_status("identity") == 200 and stack.ready_status("integration") == 200


# ── C. Dừng Identity: service khác không sập; token đã cấp vẫn verify OFFLINE ──────────────────────────────────

def test_stopping_identity_leaves_issued_tokens_valid_offline_and_machine_ingest_working(stack, api, world, seeded, guard):
    owner = world["owner"]
    token = owner["headers"]
    # JWKS đã được warm-up qua prefetch() trong lifespan → không cần bước "làm ấm" thủ công.
    stack.stop("identity")
    assert not stack.alive("identity")
    # user plane: token đã cấp vẫn qua được xác thực ở Analytics và Reward (JWKS cache + verify offline)
    assert total(api, {"headers": token}) == seeded["views"]
    r = httpx.get(stack.url("gateway", "/v1/reward/admin/accounts"), headers=token, timeout=10)
    assert r.status_code == 403, (r.status_code, r.text)           # 403 (thiếu quyền) chứ KHÔNG phải 401/5xx => token đã được xác minh
    # machine plane: API key do Integration tự xác thực, không cần Identity
    expected = ingest_more(world, seeded)
    wait_for(lambda: total(api, {"headers": token}) == expected, timeout=30, what="ingest -> analytics khi Identity chết")
    # chức năng cần Identity thì lỗi rõ ràng, nhanh
    started = time.time()
    r = httpx.post(stack.url("gateway", "/v1/auth/login"), json={"username": "e2eowner", "password": "x"}, timeout=15)
    assert r.status_code in {502, 503, 504} and time.time() - started < 12
    for name in ("integration", "analytics", "reward", "gateway"):
        assert stack.alive(name)
    assert stack.ready_status("integration") == 200 and stack.ready_status("analytics") == 200
    stack.start("identity")
    login_ok(api)                                                 # tự khỏi


# ── D. Redis không khả dụng: đường bền (PostgreSQL) vẫn đúng ────────────────────────────────────────────────────

def test_redis_unavailable_falls_back_to_postgres_and_durable_path_works(stack, api, world, seeded, guard):
    owner = world["owner"]
    restart_with(stack, "analytics", ANALYTICS_REDIS_URL="redis://analytics_cache:khong-dung@127.0.0.1:1/0")
    r = httpx.get(stack.url("analytics", "/health/ready"), timeout=5)
    assert r.status_code == 200                                    # ready không phụ thuộc Redis
    wait_for(lambda: total(api, owner) == seeded["views"], timeout=30, what="đọc thẳng PostgreSQL khi Redis chết")
    resp = api.http.get("/v1/analytics/summary", headers=owner["headers"])
    assert resp.headers.get("x-cache", "OFF") != "HIT"
    expected = ingest_more(world, seeded)                          # ghi bền vẫn chạy
    wait_for(lambda: total(api, owner) == expected, timeout=30, what="cập nhật mới hiện ngay cả khi không có cache")
    restore(stack, "analytics")
    wait_for(lambda: total(api, owner) == expected, timeout=30, what="khôi phục Redis")


# ── E. Database của MỘT service chết: service không liên quan vẫn khoẻ ──────────────────────────────────────────

@pytest.mark.parametrize("victim", ["integration", "analytics", "identity"])
def test_one_service_database_down_does_not_affect_unrelated_services(stack, api, world, seeded, guard, victim):
    owner = world["owner"]
    dead = f"postgresql://{victim}_svc:x@127.0.0.1:1/{victim}_db"
    restart_with(stack, victim, **{f"{victim.upper()}_DATABASE_URL": dead})
    try:
        assert stack.alive(victim)                                          # không crash
        assert stack.ready_status(victim) == 503                            # nhưng báo chưa sẵn sàng
        survivors = [n for n in ("identity", "integration", "analytics", "reward") if n != victim]
        for n in survivors:
            assert stack.alive(n)
        if victim != "identity":
            login_ok(api)
        else:
            r = httpx.post(stack.url("gateway", "/v1/auth/login"), json={"username": "e2eowner", "password": "x"}, timeout=20)
            assert r.status_code in {500, 502, 503, 504}
        if victim != "integration":
            expected = ingest_more(world, seeded)
            if victim != "analytics":
                wait_for(lambda: total(api, owner) == expected, timeout=30, what="analytics khi DB service khác chết")
        else:
            with pytest.raises(Exception):
                world["tool"].push(snapshot_type="delta", content=[post(99, 1)], resources=[])
            assert total(api, owner) == seeded["views"]                     # Dashboard vẫn đọc dữ liệu đã có
        if victim != "analytics":
            assert stack.ready_status("analytics") == 200
    finally:
        restore(stack, victim)
    if victim == "analytics":
        wait_for(lambda: total(api, owner) == seeded["views"], timeout=40, what="analytics đuổi kịp sau khi DB sống lại")


# ── F. Service A không đọc được database của service B ──────────────────────────────────────────────────────────

SERVICES = ["identity", "integration", "analytics", "reward"]


@pytest.mark.parametrize("who,target", [(a, b) for a in SERVICES for b in SERVICES if a != b])
def test_cross_service_database_access_is_denied(who, target):
    async def attempt():
        dsn = devenv.pg_dsn(who, database=f"{target}_db")
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        finally:
            await conn.close()

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.InvalidAuthorizationSpecificationError)):
        asyncio.run(attempt())


def test_each_service_user_works_on_its_own_database_only():
    async def own(svc):
        conn = await asyncpg.connect(devenv.pg_dsn(svc))
        try:
            is_super = await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            return is_super
        finally:
            await conn.close()
    for svc in SERVICES:
        assert asyncio.run(own(svc)) is False
