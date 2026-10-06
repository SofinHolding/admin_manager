"""Test migration: tạo đủ bảng `reward_*` + sổ sách consumer, idempotent, chạy được trên DB RỖNG không có bảng Identity."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reward_service.store import migrate  # noqa: E402
from reward_service.store.pool import Pool  # noqa: E402
from tests.conftest import TEST_DATABASE_URL  # noqa: E402

_MIGRATIONS = sorted(p.stem for p in (Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"))
ALL_VERSIONS = _MIGRATIONS
LATEST = _MIGRATIONS[-1]

REWARD_TABLES = [
    "reward_schema_migrations", "reward_discord_credentials", "reward_jobs", "reward_items",
    "reward_attempts", "reward_distributions", "reward_validation_issues", "reward_job_events",
    "reward_runner_lock", "reward_user_map", "reward_role_audit", "reward_discord_tokens",
]


BOOKKEEPING_TABLES = ["feed_cursor", "processed_events", "dead_letters", "account_projection", "account_denylist"]
IDENTITY_TABLES = ("accounts", "invite_keys", "refresh_tokens")


async def _identity_tables_present(conn) -> list[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name = ANY($1::text[])",
        list(IDENTITY_TABLES))
    return [r["table_name"] for r in rows]


async def test_migrate_creates_reward_and_bookkeeping_tables_and_is_idempotent(db_conn):
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    try:
        version1 = await migrate.run(pool)
        assert version1 == LATEST

        async with pool.acquire() as conn:
            versions = [r["version"] for r in await conn.fetch(
                "SELECT version FROM reward_schema_migrations ORDER BY version")]
            assert versions == ALL_VERSIONS

            for table in REWARD_TABLES + BOOKKEEPING_TABLES:
                exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")
                assert exists, f"bảng {table} chưa được tạo"

            table_count_1 = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name LIKE 'reward_%'")

        # Chạy lần 2 — idempotent, không lỗi, không tạo thêm bảng.
        version2 = await migrate.run(pool)
        assert version2 == LATEST

        async with pool.acquire() as conn:
            table_count_2 = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name LIKE 'reward_%'")
            migration_rows = await conn.fetchval("SELECT count(*) FROM reward_schema_migrations")

        assert table_count_1 == table_count_2 == len(REWARD_TABLES)
        assert migration_rows == len(ALL_VERSIONS)  # ON CONFLICT DO NOTHING — không nhân đôi dòng version

        assert await _identity_tables_present(db_conn) == [], "reward_db không được chứa bảng của Identity"
    finally:
        await pool.close()


async def test_002_moves_legacy_token_into_tokens_table(db_conn):
    """Dữ liệu thật: mỗi credential cũ (1 token) phải thành đúng 1 dòng ở `reward_discord_tokens`, token
    giữ nguyên ciphertext, `status` chỉ 'valid' khi cấu hình cũ đang 'valid'; cột token bị gỡ khỏi credentials."""
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    try:
        await migrate.run(pool)
        # Dựng lại hình dạng schema TRƯỚC 002 (cột token trong credentials) cùng 2 credential cũ.
        await db_conn.execute("DELETE FROM reward_discord_tokens")
        await db_conn.execute("DELETE FROM reward_discord_credentials")
        await db_conn.execute(
            "ALTER TABLE reward_discord_credentials "
            "ADD COLUMN token_ciphertext text NOT NULL DEFAULT '', ADD COLUMN key_version integer NOT NULL DEFAULT 1, "
            "ADD COLUMN discord_user_id text, ADD COLUMN discord_username text")
        await db_conn.execute(
            "INSERT INTO reward_discord_credentials (account_id, token_ciphertext, discord_user_id, discord_username, "
            "status, last_error) VALUES ('acc-valid','ct-valid','u1','one','valid',NULL), "
            "('acc-bad','ct-bad','u2','two','invalid','Không tìm thấy lệnh /give-xp trong guild')")
        await db_conn.execute("DELETE FROM reward_schema_migrations WHERE version='002_reward_tokens'")

        assert await migrate.run(pool) == LATEST

        rows = {r["account_id"]: r for r in await db_conn.fetch("SELECT * FROM reward_discord_tokens")}
        assert rows["acc-valid"]["token_ciphertext"] == "ct-valid" and rows["acc-valid"]["status"] == "valid"
        assert rows["acc-bad"]["token_ciphertext"] == "ct-bad" and rows["acc-bad"]["status"] == "unverified"
        cols = {r["column_name"] for r in await db_conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='reward_discord_credentials'")}
        assert "token_ciphertext" not in cols and "discord_user_id" not in cols
    finally:
        await pool.close()


async def test_all_migrations_apply_on_empty_database_without_identity_tables(db_conn):
    """Dựng lại từ DB RỖNG (xoá mọi bảng trong reward_db — DB dành riêng cho Reward): 001→004 áp được mà không cần
    bảng `accounts`; sau đó không có bảng Identity nào và migration cuối là 004."""
    names = [r["tablename"] for r in await db_conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
    for name in names:
        await db_conn.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    try:
        assert await migrate.run(pool) == LATEST == "004_reward_identity_link"
        assert await _identity_tables_present(db_conn) == []
        for table in REWARD_TABLES + BOOKKEEPING_TABLES:
            assert await db_conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}"), table
        # account_projection đúng 4 cột nghiệp vụ theo hợp đồng (+ reward_access, updated_at); denylist có khoá chống trùng
        cols = {r["column_name"] for r in await db_conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='account_projection'")}
        assert {"account_id", "username", "role", "status"} <= cols
    finally:
        await pool.close()


def test_no_migration_creates_references_or_alters_identity_tables():
    import re
    for path in (Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"):
        sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))  # bỏ comment (chỉ nhắc FK mềm)
        for table in IDENTITY_TABLES:
            pattern = rf"\b(REFERENCES|ALTER\s+TABLE|CREATE\s+TABLE(\s+IF\s+NOT\s+EXISTS)?|FROM|JOIN)\s+{table}\b"
            assert not re.search(pattern, sql, re.I), f"{path.name} chạm bảng {table}"
