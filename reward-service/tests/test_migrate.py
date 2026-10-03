"""Test migration: tạo đủ 12 bảng `reward_*`, idempotent, không đụng DDL của admin_manager."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import migrate  # noqa: E402
from store.pool import Pool  # noqa: E402
from tests.conftest import TEST_DATABASE_URL  # noqa: E402

_MIGRATIONS = sorted(p.stem for p in (Path(__file__).resolve().parents[1] / "store" / "migrations").glob("*.sql"))
ALL_VERSIONS = _MIGRATIONS
LATEST = _MIGRATIONS[-1]

REWARD_TABLES = [
    "reward_schema_migrations", "reward_discord_credentials", "reward_jobs", "reward_items",
    "reward_attempts", "reward_distributions", "reward_validation_issues", "reward_job_events",
    "reward_runner_lock", "reward_user_map", "reward_role_audit", "reward_discord_tokens",
]


async def _admin_manager_columns(conn) -> list[tuple]:
    rows = await conn.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_name IN ('accounts','invite_keys','refresh_tokens') "
        "ORDER BY table_name, column_name")
    return [(r["table_name"], r["column_name"], r["data_type"]) for r in rows]


async def test_migrate_creates_12_reward_tables_and_is_idempotent(db_conn):
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    try:
        before = await _admin_manager_columns(db_conn)

        version1 = await migrate.run(pool)
        assert version1 == LATEST

        async with pool.acquire() as conn:
            versions = [r["version"] for r in await conn.fetch(
                "SELECT version FROM reward_schema_migrations ORDER BY version")]
            assert versions == ALL_VERSIONS

            for table in REWARD_TABLES:
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

        after = await _admin_manager_columns(db_conn)
        assert before == after, "DDL accounts/invite_keys/refresh_tokens của admin_manager bị thay đổi!"
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
