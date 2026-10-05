"""Cô lập dữ liệu ở tầng hạ tầng: DB + user riêng cho từng service, Redis ACL theo prefix."""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from archlib import ROOT, load_registry

REGISTRY = load_registry()
INITDB = (ROOT / "infra" / "docker" / "initdb" / "01-databases.sql").read_text(encoding="utf-8")
ACL = (ROOT / "infra" / "docker" / "redis" / "users.acl").read_text(encoding="utf-8")
COMPOSE = (ROOT / "infra" / "docker" / "compose.dev.yml").read_text(encoding="utf-8")


def role_of(db: str) -> str:
    return db.replace("_db", "_svc")


@pytest.mark.parametrize("name", sorted(REGISTRY["services"]))
def test_database_and_dedicated_role_exist(name):
    db = REGISTRY["services"][name]["database"]
    role = role_of(db)
    assert re.search(rf"CREATE ROLE {role}\s+LOGIN", INITDB), f"thiếu role {role}"
    assert re.search(rf"CREATE DATABASE {db}\s+OWNER {role}", INITDB), f"thiếu {db} thuộc {role}"
    assert re.search(rf"REVOKE ALL ON DATABASE {db}\s+FROM PUBLIC", INITDB), f"{db} còn mở cho PUBLIC"
    assert re.search(rf"GRANT CONNECT ON DATABASE {db}\s+TO {role};", INITDB)


def test_no_cross_database_grants():
    """Mỗi lệnh GRANT trên một database chỉ được trao cho role sở hữu database đó."""
    for db, role in re.findall(r"GRANT\s+\w+\s+ON DATABASE (\w+)\s+TO (\w+)", INITDB):
        assert role == role_of(db), f"{role} được cấp quyền trên {db}"


def test_service_roles_are_not_privileged():
    for line in re.findall(r"CREATE ROLE .*", INITDB):
        assert "NOSUPERUSER" in line and "NOCREATEDB" in line and "NOCREATEROLE" in line, line
        assert "SUPERUSER" not in line.replace("NOSUPERUSER", "")


def test_no_extra_databases_beyond_registry():
    created = set(re.findall(r"CREATE DATABASE (\w+)", INITDB))
    assert created == {s["database"] for s in REGISTRY["services"].values()}


def test_redis_default_user_is_disabled():
    assert re.search(r"^user default off\b", ACL, re.M)


def test_redis_users_are_prefix_scoped_and_match_cache_services():
    users = re.findall(r"^user (\w+) on .*", ACL, re.M)
    cache_services = {n.replace("-service", "") for n, s in REGISTRY["services"].items()
                      if "cache" in s["modules"]}
    assert {u.replace("_cache", "") for u in users} == cache_services
    for line in re.findall(r"^user (?!default).*", ACL, re.M):
        user = line.split()[1]
        prefix = user.replace("_cache", "")
        assert f"~{prefix}:*" in line, f"{user} không bị giới hạn theo prefix"
        assert "~*" not in line.split() and "allkeys" not in line, f"{user} thấy mọi key"
        assert "-@dangerous" in line, f"{user} còn lệnh nguy hiểm (FLUSHALL, KEYS...)"


def test_compose_has_unique_project_name():
    """Không có `name:` riêng thì Compose dùng tên thư mục (`docker`) và có thể ghi đè container của
    project khác trùng tên service (đã xảy ra với `redis` của RAGFlow)."""
    m = re.search(r"^name:\s*(\S+)", COMPOSE, re.M)
    assert m, "compose.dev.yml thiếu `name:` riêng"
    assert m.group(1) not in {"docker", "compose", "infra", "default"}, m.group(1)
    assert m.group(1).startswith("manager-platform")


def test_compose_host_ports_are_not_common_defaults():
    """Cổng host mặc định (5432, 6379...) dễ đụng dịch vụ đang chạy sẵn trên máy dev."""
    defaults = {5432, 6379, 3306, 9200, 3000, 8000, 8080}
    host_ports = [int(m) for m in re.findall(r'"127\.0\.0\.1:(\d+):\d+"', COMPOSE)]
    assert host_ports, "không đọc được cổng host (đổi định dạng?)"
    assert not set(host_ports) & defaults, host_ports


def test_port_check_is_exact_not_substring():
    assert [int(m) for m in re.findall(r'"127\.0\.0\.1:(\d+):\d+"', '["127.0.0.1:55432:5432"]')] == [55432]


def test_compose_names_every_resource_explicitly_with_project_prefix():
    """Tên tường minh có tiền tố riêng: không thể trùng/ghi đè container, volume, network của project khác."""
    containers = re.findall(r"container_name:\s*(\S+)", COMPOSE)
    assert len(containers) == len(re.findall(r"^  (?:postgres|redis):", COMPOSE, re.M)) >= 2
    assert all(c.startswith("mp-dev-") for c in containers), containers
    explicit = re.findall(r"^    name:\s*(\S+)", COMPOSE, re.M)
    assert explicit and all(n.startswith("mp_dev_") for n in explicit), explicit
    declared = set(re.findall(r"^  (mp_dev_\w+):", COMPOSE, re.M))
    assert declared == set(explicit), "volume/network khai báo mà thiếu `name:` tường minh"


def test_compose_never_attaches_to_external_resources():
    assert "external:" not in COMPOSE, "không được dùng volume/network của project khác"
    assert "network_mode" not in COMPOSE


def test_redis_acl_file_has_no_comments_or_blank_directives():
    """Redis từ chối khởi động nếu file ACL có dòng không bắt đầu bằng `user` (kể cả chú thích #)."""
    lines = [ln for ln in ACL.splitlines() if ln.strip()]
    assert lines and all(ln.startswith("user ") for ln in lines), lines


def test_container_files_are_pinned_to_lf():
    """Git trên Windows có thể đổi sang CRLF; Redis từ chối file ACL có ."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for pattern in ("*.acl text eol=lf", "*.sql text eol=lf", "Dockerfile text eol=lf"):
        assert pattern in attrs, pattern
    acl_bytes = (ROOT / "infra" / "docker" / "redis" / "users.acl").read_bytes()
    assert b"" not in acl_bytes


def test_compose_mounts_isolation_config():
    assert "./initdb:/docker-entrypoint-initdb.d" in COMPOSE
    assert "--aclfile" in COMPOSE
    assert "127.0.0.1:" in COMPOSE  # không mở DB/Redis ra mạng ngoài


# ── Cần Postgres thật ───────────────────────────────────────────────────────────

PG_REASON = ("cần Postgres đã khởi tạo bằng infra/docker/initdb (docker compose up). "
             "Đặt TEST_PG_HOST/TEST_PG_PORT để chạy. KHÔNG được coi là đã kiểm chứng khi SKIP.")


@pytest.mark.requires_postgres
@pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason=PG_REASON)
def test_service_role_cannot_connect_to_another_services_database():
    """Bài kiểm tra D (thu nhỏ): thu hồi quyền DB chéo — identity_svc không vào được analytics_db."""
    import asyncpg

    host, port = os.environ["TEST_PG_HOST"], int(os.environ.get("TEST_PG_PORT", "5432"))

    async def attempt(user: str, db: str) -> str:
        try:
            conn = await asyncpg.connect(host=host, port=port, user=user,
                                         password=f"dev_{user.replace('_svc', '')}_change_me",
                                         database=db, timeout=5)
            await conn.close()
            return "connected"
        except asyncpg.InsufficientPrivilegeError:
            return "denied"

    assert asyncio.run(attempt("identity_svc", "identity_db")) == "connected"
    for svc in REGISTRY["services"].values():
        if svc["database"] != "identity_db":
            assert asyncio.run(attempt("identity_svc", svc["database"])) == "denied", svc["database"]


REDIS_REASON = ("cần Redis đã khởi tạo bằng infra/docker (docker compose up). "
                "Đặt TEST_REDIS_HOST/TEST_REDIS_PORT để chạy. KHÔNG được coi là đã kiểm chứng khi SKIP.")


@pytest.mark.skipif(not os.environ.get("TEST_REDIS_HOST"), reason=REDIS_REASON)
def test_redis_acl_enforces_prefix_and_blocks_dangerous_commands():
    """Redis thật: analytics chỉ chạm key `analytics:*`, không FLUSHALL/KEYS, user `default` bị tắt."""
    import redis

    host, port = os.environ["TEST_REDIS_HOST"], int(os.environ.get("TEST_REDIS_PORT", "6379"))
    ok = redis.Redis(host=host, port=port, username="analytics_cache",
                     password="dev_analytics_change_me", decode_responses=True, socket_timeout=3)
    assert ok.ping()
    assert ok.set("analytics:acl-test", "1", ex=30)
    assert ok.get("analytics:acl-test") == "1"
    assert ok.incr("analytics:acl-seal") >= 1  # seal phiên bản cho cache-aside
    ok.delete("analytics:acl-test", "analytics:acl-seal")

    with pytest.raises(redis.exceptions.NoPermissionError):
        ok.set("identity:forbidden", "x")  # key ngoài prefix của service
    with pytest.raises(redis.exceptions.NoPermissionError):
        ok.flushall()
    with pytest.raises(redis.exceptions.NoPermissionError):
        ok.keys("*")

    anon = redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=3)
    with pytest.raises(redis.exceptions.AuthenticationError):
        anon.ping()  # user default đã tắt
