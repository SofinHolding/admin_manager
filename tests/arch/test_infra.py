"""Cô lập dữ liệu ở tầng hạ tầng + vệ sinh secret: DB/user riêng cho từng service, Redis ACL theo prefix,
không secret nào nằm trong repo."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys

import pytest
from archlib import ROOT, load_registry

sys.path.insert(0, str(ROOT / "infra" / "scripts"))
import bootstrap_dev  # noqa: E402
import devenv  # noqa: E402

REGISTRY = load_registry()
TPL_DIR = ROOT / "infra" / "docker" / "templates"
INITDB = (TPL_DIR / "initdb" / "01-databases.sql.tpl").read_text(encoding="utf-8")
ACL = (TPL_DIR / "redis" / "users.acl.tpl").read_text(encoding="utf-8")
COMPOSE = (ROOT / "infra" / "docker" / "compose.dev.yml").read_text(encoding="utf-8")


def role_of(db: str) -> str:
    return db.replace("_db", "_svc")


# ── Template Postgres ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(n for n, s in REGISTRY["services"].items() if s["database"]))
def test_database_and_dedicated_role_exist(name):
    db = REGISTRY["services"][name]["database"]
    role = role_of(db)
    assert re.search(rf"CREATE ROLE {role}\s+LOGIN", INITDB), f"thiếu role {role}"
    assert re.search(rf"CREATE DATABASE {db}\s+OWNER {role}", INITDB), f"thiếu {db} thuộc {role}"
    assert re.search(rf"REVOKE ALL ON DATABASE {db}\s+FROM PUBLIC", INITDB), f"{db} còn mở cho PUBLIC"
    assert re.search(rf"GRANT CONNECT ON DATABASE {db}\s+TO {role};", INITDB)


def test_no_cross_database_grants():
    for db, role in re.findall(r"GRANT\s+\w+\s+ON DATABASE (\w+)\s+TO (\w+)", INITDB):
        assert role == role_of(db), f"{role} được cấp quyền trên {db}"


def test_service_roles_are_not_privileged():
    for line in re.findall(r"CREATE ROLE .*", INITDB):
        assert "NOSUPERUSER" in line and "NOCREATEDB" in line and "NOCREATEROLE" in line, line


def test_no_extra_databases_beyond_registry():
    created = set(re.findall(r"CREATE DATABASE (\w+)", INITDB))
    assert created == {s["database"] for s in REGISTRY["services"].values() if s["database"]}


def test_templates_use_placeholders_never_literal_passwords():
    for text in (INITDB, ACL):
        for pw in re.findall(r"PASSWORD '([^']*)'", text) + re.findall(r">(\S+)", text):
            assert pw.startswith("${") and pw.endswith("}"), f"mật khẩu viết cứng trong template: {pw!r}"


# ── Redis ACL template ──────────────────────────────────────────────────────────

def test_redis_default_user_is_disabled():
    assert re.search(r"^user default off\b", ACL, re.M)


def test_redis_users_are_prefix_scoped_and_match_cache_services():
    users = re.findall(r"^user (\w+) on .*", ACL, re.M)
    cache_services = {n.replace("-service", "") for n, s in REGISTRY["services"].items() if "cache" in s["modules"]}
    assert {u.replace("_cache", "") for u in users} == cache_services
    for line in re.findall(r"^user (?!default).*", ACL, re.M):
        user = line.split()[1]
        prefix = user.replace("_cache", "")
        assert f"~{prefix}:*" in line, f"{user} không bị giới hạn theo prefix"
        assert "~*" not in line.split() and "allkeys" not in line, f"{user} thấy mọi key"
        assert "-@dangerous" in line, f"{user} còn lệnh nguy hiểm (FLUSHALL, KEYS...)"


def test_redis_acl_template_has_no_comments_or_blank_directives():
    """Redis từ chối khởi động nếu file ACL có dòng không bắt đầu bằng `user` (kể cả chú thích #)."""
    lines = [ln for ln in ACL.splitlines() if ln.strip()]
    assert lines and all(ln.startswith("user ") for ln in lines), lines


# ── Compose ─────────────────────────────────────────────────────────────────────

def test_compose_has_unique_project_name():
    m = re.search(r"^name:\s*(\S+)", COMPOSE, re.M)
    assert m and m.group(1).startswith("manager-platform") and m.group(1) not in {"docker", "compose", "infra"}


def test_compose_host_ports_are_not_common_defaults_and_bind_localhost():
    defaults = {5432, 6379, 3306, 9200, 3000, 8000, 8080}
    ports = re.findall(r'"(\d+\.\d+\.\d+\.\d+):(\d+):\d+"', COMPOSE)
    assert ports and all(host == "127.0.0.1" for host, _ in ports), "DB/Redis phải chỉ nghe trên 127.0.0.1"
    assert not {int(p) for _, p in ports} & defaults


def test_compose_names_every_resource_explicitly_with_project_prefix():
    containers = re.findall(r"container_name:\s*(\S+)", COMPOSE)
    assert len(containers) >= 2 and all(c.startswith("mp-dev-") for c in containers), containers
    explicit = re.findall(r"^    name:\s*(\S+)", COMPOSE, re.M)
    assert explicit and all(n.startswith("mp_dev_") for n in explicit), explicit
    assert set(re.findall(r"^  (mp_dev_\w+):", COMPOSE, re.M)) == set(explicit)


def test_compose_never_attaches_to_external_resources_or_host_paths():
    assert "external:" not in COMPOSE and "network_mode" not in COMPOSE
    assert "docker.sock" not in COMPOSE, "không mount Docker socket"
    assert "privileged" not in COMPOSE


def test_compose_secrets_come_from_env_not_literals():
    assert "${DEV_PG_ADMIN_PASSWORD" in COMPOSE and "${DEV_REDIS_ANALYTICS_PASSWORD" in COMPOSE
    assert not re.search(r"PASSWORD:\s*[A-Za-z0-9_]{6,}\s*$", COMPOSE, re.M), "mật khẩu viết cứng trong compose"


def test_compose_mounts_generated_isolation_config():
    assert ".dev/initdb:/docker-entrypoint-initdb.d" in COMPOSE
    assert "--aclfile" in COMPOSE


def test_container_files_are_pinned_to_lf():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for pattern in ("*.acl text eol=lf", "*.sql text eol=lf", "Dockerfile text eol=lf"):
        assert pattern in attrs, pattern


# ── Vệ sinh secret ──────────────────────────────────────────────────────────────

def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    return [f for f in out.splitlines() if f]


SECRET_PATTERNS = [
    (r"-----BEGIN (RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----", "private key"),
    (r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][A-Za-z0-9_\-+/=]{20,}['\"]", "secret gán cứng"),
    (r"postgres(?:ql)?://[^:@\s/]+:(?!(?:MAT_KHAU|CHANGE_ME|PASSWORD|password|<)[^@]*@)[^@\s${}<>*]{6,}@(?!\$)",
     "DSN chứa mật khẩu"),
    (r"\bdev-secret-thay-doi-khi-deploy\b", "JWT secret mặc định không an toàn"),
    (r"\bdev_[a-z]+_change_me\b", "mật khẩu DEV mặc định cũ"),
]
# Chỉ những chỗ CỐ Ý nhắc tới chuỗi cấm (tài liệu, test kiểm tra chính quy tắc này).
# Lý do từng ngoại lệ: tài liệu/test nhắc chính chuỗi cấm; CI chạy Postgres dùng-một-lần trên runner GitHub.
ALLOW_MENTION = {".github/workflows/deploy-reward.yml", "reward-service/scripts/check-env.py", "docs/operations/dev-agent-full-implementation-prompt.md",
                 "tasks/backlog/dev-agent-full-execution-master-prompt.md",
                 "tests/arch/test_infra.py", "infra/scripts/bootstrap_dev.py", "tests/arch/test_security_hygiene.py"}


def test_no_secret_patterns_in_tracked_files():
    hits = []
    for rel in _tracked_files():
        if rel in ALLOW_MENTION or rel.startswith(("docs/api/legacy/", "admin-web/node_modules/")):
            continue
        if rel.endswith((".png", ".ico", ".lock", ".html", ".svg", ".woff", ".woff2")) or "package-lock" in rel:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, FileNotFoundError):
            continue
        for pat, label in SECRET_PATTERNS:
            if re.search(pat, text):
                hits.append(f"{rel}: {label}")
    assert not hits, "secret trong file đã commit:\n" + "\n".join(hits)


def test_dev_secret_files_are_gitignored_and_untracked():
    tracked = set(_tracked_files())
    assert ".env.dev" not in tracked and not any(f.startswith(".dev/") for f in tracked)
    for path in (".env.dev", ".dev/initdb/01-databases.sql"):
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert r.returncode == 0, f"{path} chưa nằm trong .gitignore"


def test_bootstrap_generates_strong_random_unique_secrets():
    a, b = bootstrap_dev.generate(), bootstrap_dev.generate()
    assert not bootstrap_dev.missing_or_weak(a)
    for key, value in a.items():
        if key in bootstrap_dev.CONSTANTS:
            continue
        assert value != b[key], f"{key} không ngẫu nhiên giữa hai lần sinh"
        assert len(value) >= 24
    secrets_ = [v for k, v in a.items() if k not in bootstrap_dev.CONSTANTS]
    assert len(secrets_) == len(set(secrets_)), "hai secret trùng nhau"


def test_weak_or_default_secrets_are_flagged():
    weak = bootstrap_dev.generate()
    weak["DEV_PG_IDENTITY_PASSWORD"] = "dev_identity_change_me"
    weak["IDENTITY_FEED_KEY_ANALYTICS"] = "ngan"
    problems = " ".join(bootstrap_dev.missing_or_weak(weak))
    assert "DEV_PG_IDENTITY_PASSWORD" in problems and "IDENTITY_FEED_KEY_ANALYTICS" in problems


def test_rendered_files_have_no_unresolved_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap_dev, "GEN_DIR", tmp_path)
    bootstrap_dev.render_templates(bootstrap_dev.generate())
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert "${" not in f.read_text(encoding="utf-8")


# ── Cần hạ tầng DEV thật ────────────────────────────────────────────────────────

needs_pg = pytest.mark.skipif(not devenv.pg_ready(), reason=devenv.SKIP_REASON)
needs_redis = pytest.mark.skipif(not devenv.redis_ready(), reason=devenv.SKIP_REASON)


@needs_pg
def test_every_service_role_is_denied_on_every_other_services_database():
    """Thu hồi quyền DB chéo: 4 user x 3 DB khác = 12 kết nối đều phải bị từ chối; DB của mình thì vào được."""
    import asyncpg

    services = ["identity", "integration", "analytics", "reward"]

    async def attempt(user: str, db: str) -> str:
        try:
            conn = await asyncpg.connect(devenv.pg_dsn(user, database=db), timeout=5)
            await conn.close()
            return "connected"
        except asyncpg.InsufficientPrivilegeError:
            return "denied"

    async def run():
        return {(u, d): await attempt(u, f"{d}_db") for u in services for d in services}

    for (u, d), outcome in asyncio.run(run()).items():
        assert outcome == ("connected" if u == d else "denied"), f"{u}_svc -> {d}_db: {outcome}"


@needs_pg
def test_service_roles_have_no_superuser_or_create_privileges():
    import asyncpg

    async def run():
        conn = await asyncpg.connect(devenv.pg_dsn(admin=True))
        rows = await conn.fetch("SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                                "FROM pg_roles WHERE rolname LIKE '%\\_svc'")
        await conn.close()
        return rows

    rows = asyncio.run(run())
    assert len(rows) == 4
    for r in rows:
        assert not (r["rolsuper"] or r["rolcreatedb"] or r["rolcreaterole"] or r["rolreplication"]), r["rolname"]


@needs_pg
def test_postgres_is_not_reachable_with_a_guessable_password():
    import asyncpg

    async def run(pw):
        try:
            conn = await asyncpg.connect(host="127.0.0.1", port=devenv.pg_host_port()[1], user="postgres",
                                         password=pw, database="postgres", timeout=3)
            await conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    for guess in ("postgres", "password", "dev_postgres_admin_change_me", "admin", ""):
        assert not asyncio.run(run(guess)), f"đăng nhập được bằng mật khẩu đoán được: {guess!r}"


@needs_redis
def test_redis_acl_enforces_prefix_and_blocks_dangerous_commands():
    import redis

    env = devenv.load()
    host, port = env.get("DEV_REDIS_HOST", "127.0.0.1"), int(env["DEV_REDIS_PORT"])
    ok = redis.Redis(host=host, port=port, username="analytics_cache",
                     password=env["DEV_REDIS_ANALYTICS_PASSWORD"], decode_responses=True, socket_timeout=3)
    assert ok.ping()
    assert ok.set("analytics:acl-test", "1", ex=30) and ok.get("analytics:acl-test") == "1"
    assert ok.incr("analytics:acl-seal") >= 1
    ok.delete("analytics:acl-test", "analytics:acl-seal")
    for bad in (lambda: ok.set("identity:forbidden", "x"), ok.flushall, lambda: ok.keys("*")):
        with pytest.raises(redis.exceptions.NoPermissionError):
            bad()
    anon = redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=3)
    with pytest.raises(redis.exceptions.AuthenticationError):
        anon.ping()
