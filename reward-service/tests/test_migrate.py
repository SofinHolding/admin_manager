"""Test migration: tạo đủ 11 bảng `reward_*`, idempotent, không đụng DDL của admin_manager."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import migrate  # noqa: E402
from store.pool import Pool  # noqa: E402
from tests.conftest import TEST_DATABASE_URL  # noqa: E402

REWARD_TABLES = [
    "reward_schema_migrations", "reward_discord_credentials", "reward_jobs", "reward_items",
    "reward_attempts", "reward_distributions", "reward_validation_issues", "reward_job_events",
    "reward_runner_lock", "reward_user_map", "reward_role_audit",
]


async def _admin_manager_columns(conn) -> list[tuple]:
    rows = await conn.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_name IN ('accounts','invite_keys','refresh_tokens') "
        "ORDER BY table_name, column_name")
    return [(r["table_name"], r["column_name"], r["data_type"]) for r in rows]


async def test_migrate_creates_11_reward_tables_and_is_idempotent(db_conn):
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    try:
        before = await _admin_manager_columns(db_conn)

        version1 = await migrate.run(pool)
        assert version1 == "001_reward_init"

        async with pool.acquire() as conn:
            row = await conn.fetchval("SELECT version FROM reward_schema_migrations")
            assert row == "001_reward_init"

            for table in REWARD_TABLES:
                exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")
                assert exists, f"bảng {table} chưa được tạo"

            table_count_1 = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name LIKE 'reward_%'")

        # Chạy lần 2 — idempotent, không lỗi, không tạo thêm bảng.
        version2 = await migrate.run(pool)
        assert version2 == "001_reward_init"

        async with pool.acquire() as conn:
            table_count_2 = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name LIKE 'reward_%'")
            migration_rows = await conn.fetchval("SELECT count(*) FROM reward_schema_migrations")

        assert table_count_1 == table_count_2 == len(REWARD_TABLES)
        assert migration_rows == 1  # ON CONFLICT DO NOTHING — không nhân đôi dòng version

        after = await _admin_manager_columns(db_conn)
        assert before == after, "DDL accounts/invite_keys/refresh_tokens của admin_manager bị thay đổi!"
    finally:
        await pool.close()
