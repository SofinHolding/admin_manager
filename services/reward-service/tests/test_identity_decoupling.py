"""Phase 4 — chứng minh Reward HẾT coupling với Identity/admin_manager.

  * `reward_db` không có bảng `accounts`/`invite_keys`/`refresh_tokens`; không mã nguồn nào của Reward tham chiếu chúng;
  * user `reward_svc` bị Postgres từ chối khi kết nối DB của service khác (kể cả `identity_db`);
  * không còn JWT secret chung / HS256; token Discord vẫn mã hoá Fernet bằng `REWARD_TOKEN_KEY` từ env.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import devenv  # noqa: E402
from tests.conftest import HAVE_DB, TEST_DATABASE_URL  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent.parent
IDENTITY_TABLES = ("accounts", "invite_keys", "refresh_tokens", "permissions", "account_permissions", "oidc_clients",
                   "signing_keys", "auth_audit", "sessions", "integration_provisioning")
_SQL_REF = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?|REFERENCES)\s+"
                      r"(?:[a-z_]+\.)?([a-z_][a-z0-9_]*)\b", re.I)
_SQL_VERB = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b", re.I)


def foreign_table_refs(source: str) -> set[str]:
    """Bảng của Identity bị tham chiếu trong hằng chuỗi SQL của một file Python (docstring bị bỏ qua)."""
    tree = ast.parse(source)
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if _SQL_VERB.search(node.value):
                hits |= {m.group(1).lower() for m in _SQL_REF.finditer(node.value)} & set(IDENTITY_TABLES)
    return hits


def _service_sources() -> list[Path]:
    skip = {"tests", "__pycache__", ".venv", "scripts"}
    return [p for p in ROOT.rglob("*.py") if not (set(p.relative_to(ROOT).parts) & skip)]


# ── Không còn bảng / mã SQL của Identity ─────────────────────────────────────────

def test_no_reward_source_references_identity_tables():
    violations = {str(p.relative_to(ROOT)): sorted(foreign_table_refs(p.read_text(encoding="utf-8")))
                  for p in _service_sources() if foreign_table_refs(p.read_text(encoding="utf-8"))}
    assert not violations, violations


def test_accounts_repository_is_gone():
    assert not (ROOT / "src" / "reward_service" / "store" / "repositories" / "accounts.py").exists()


def test_checker_detects_identity_table_access():
    """Test của test: bộ quét phải bắt được vi phạm thật và không báo oan."""
    assert foreign_table_refs('Q = "SELECT role FROM accounts WHERE id = $1"') == {"accounts"}
    assert foreign_table_refs('Q = "UPDATE accounts SET role = \'discord\' WHERE id = $1"') == {"accounts"}
    assert foreign_table_refs('Q = "SELECT * FROM reward_jobs j LEFT JOIN invite_keys k ON true"') == {"invite_keys"}
    assert foreign_table_refs('Q = "SELECT 1 FROM refresh_tokens"') == {"refresh_tokens"}
    assert foreign_table_refs('Q = "SELECT * FROM account_projection JOIN account_denylist USING (account_id)"') == set()
    assert foreign_table_refs('MSG = "chọn accounts để xem"') == set()
    assert foreign_table_refs('def f():\n    """SELECT x FROM accounts"""\n') == set()  # docstring không phải SQL chạy


@pytest.mark.skipif(not HAVE_DB, reason=devenv.SKIP_REASON)
async def test_reward_db_has_no_identity_tables(db_conn):
    rows = await db_conn.fetch(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_name = ANY($1::text[])", list(IDENTITY_TABLES))
    assert rows == []


@pytest.mark.skipif(not HAVE_DB, reason=devenv.SKIP_REASON)
async def test_reward_runs_on_its_own_database_not_ufsync(db_conn):
    assert await db_conn.fetchval("SELECT current_database()") == "reward_db"
    assert await db_conn.fetchval("SELECT current_user") == "reward_svc"
    assert "ufsync" not in TEST_DATABASE_URL


@pytest.mark.skipif(not devenv.pg_ready(), reason=devenv.SKIP_REASON)
@pytest.mark.parametrize("other", ["identity_db", "integration_db", "analytics_db"])
async def test_reward_svc_is_denied_on_other_services_databases(other):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await asyncpg.connect(devenv.pg_dsn("reward", database=other), timeout=5)


# ── Không còn JWT secret chung ──────────────────────────────────────────────────

def test_no_symmetric_jwt_or_legacy_secret_in_sources():
    offenders = []
    for path in _service_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in ("HS256", "HS384", "HS512", "JWT_SECRET",
                                                                 "ADMIN_API_BASE"):
                offenders.append(f"{path.relative_to(ROOT)}: {node.value}")
            if isinstance(node, ast.Attribute) and node.attr == "jwt_secret":
                offenders.append(f"{path.relative_to(ROOT)}: .jwt_secret")
    assert not offenders, offenders


def test_scripts_and_env_example_have_no_shared_secret_requirement():
    for rel in ("scripts/check-env.py", ".env.example", "run.sh"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"^\s*JWT_SECRET\s*=", text, re.M), rel
        assert "dev-secret-" + "thay-doi-khi-deploy" not in text and "ADMIN_ENV_PATH" not in text, rel
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for var in ("REWARD_IDENTITY_URL", "REWARD_IDENTITY_JWKS_URL", "REWARD_ISSUER", "REWARD_AUDIENCE",
                "REWARD_IDENTITY_FEED_URL", "REWARD_IDENTITY_FEED_KEY", "REWARD_DATABASE_URL", "REWARD_TOKEN_KEY"):
        assert var in example, var


def test_requirements_pin_what_the_sdk_needs():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for pkg in ("httpx", "pyjwt", "cryptography", "fastapi", "asyncpg"):
        assert pkg in req


# ── Registry khớp thực tế ───────────────────────────────────────────────────────

def test_registry_declares_new_tables_events_and_module():
    svc = json.loads((REPO / "infra" / "service-registry.json").read_text(encoding="utf-8"))["services"]["reward-service"]
    for table in ("feed_cursor", "processed_events", "dead_letters", "account_projection", "account_denylist"):
        assert table in svc["owned_tables"]
    for event in ("account.permission_changed", "account.status_changed", "account.deleted", "session.revoked"):
        assert event in svc["consumes"]
    assert "identity_link" in svc["modules"] and (ROOT / "src" / "reward_service" / "identity_link" / "__init__.py").exists()
    assert svc["database"] == "reward_db"


def test_every_bookkeeping_table_in_migrations_is_declared_as_owned():
    svc = json.loads((REPO / "infra" / "service-registry.json").read_text(encoding="utf-8"))["services"]["reward-service"]
    owned = set(svc["owned_tables"])
    created = set()
    migrations = sorted((ROOT / "migrations").glob("*.sql"))
    assert migrations, "không tìm thấy migration (đường dẫn sai làm test đúng một cách rỗng)"
    for path in migrations:
        created |= {m.lower() for m in re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)", path.read_text(encoding="utf-8"),
                                                  re.I)}
    undeclared = {t for t in created if t not in owned and not t.startswith("reward_")}
    assert not undeclared, undeclared


# ── Token Discord vẫn mã hoá ────────────────────────────────────────────────────

async def test_discord_token_is_stored_encrypted_with_key_from_env(db_conn, identity):
    import os
    from reward_service.crypto import TokenCrypto
    from tests.conftest import insert_account, sign_token
    from tests.harness.fake_discord import start_fake

    fake = await start_fake()
    try:
        # trỏ Discord giả: tạo lại client với env Discord giả để verify token thành công
        from tests.conftest import app_client
        async with app_client(identity, REWARD_API_BASE=fake.api_base, REWARD_GATEWAY_URL=fake.gateway_url) as ac:
            user = await insert_account(db_conn, role="discord")
            headers = {"Authorization": f"Bearer {sign_token(user)}"}
            await ac.put("/v1/reward/credentials", headers=headers, json={
                "guild_id": "111111111111111111", "channel_id": "222222222222222222", "command_name": fake.command_name})
            plain = "DISCORD-PLAINTEXT-TOKEN-XYZ-123"
            resp = await ac.post("/v1/reward/credentials/tokens", headers=headers, json={"token": plain})
            assert resp.status_code == 200
            stored = await db_conn.fetchval(
                "SELECT token_ciphertext FROM reward_discord_tokens WHERE account_id = $1", user["id"])
            assert stored and plain not in stored
            assert TokenCrypto(os.environ["REWARD_TOKEN_KEY"]).decrypt(stored) == plain
            assert plain not in resp.text
    finally:
        await fake.close()


def test_missing_token_key_fails_fast(monkeypatch):
    from reward_service.settings import RewardConfigError, load_settings
    monkeypatch.delenv("REWARD_TOKEN_KEY")
    with pytest.raises(RewardConfigError, match="REWARD_TOKEN_KEY"):
        load_settings()
    monkeypatch.setenv("REWARD_TOKEN_KEY", "khong-phai-fernet")
    with pytest.raises(RewardConfigError):
        load_settings()
