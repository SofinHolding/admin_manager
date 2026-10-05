"""Migration runner trên Postgres thật."""

from __future__ import annotations

import shutil

import pytest

from identity_service import migrate

from conftest import _dsn

pytestmark = pytest.mark.needs_pg


async def test_schema_has_expected_tables_and_is_up_to_date(db):
    async with db.connection() as conn:
        tables = {r["tablename"] for r in await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        assert {"accounts", "invite_keys", "refresh_tokens", "auth_audit", "schema_migrations"} <= tables
        assert await migrate.is_up_to_date(conn)


async def test_run_is_idempotent(db):
    assert await migrate.run(_dsn()) == []


async def test_applied_migration_cannot_be_edited(db, tmp_path):
    shutil.copy(migrate.MIGRATIONS_DIR / "0001_init.sql", tmp_path / "0001_init.sql")
    with open(tmp_path / "0001_init.sql", "a", encoding="utf-8") as f:
        f.write("\n-- sửa lén\n")
    with pytest.raises(migrate.MigrationError, match="checksum"):
        await migrate.run(_dsn(), tmp_path)


async def test_invalid_migration_name_is_rejected(tmp_path):
    (tmp_path / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(migrate.MigrationError, match="Tên migration"):
        migrate.list_migrations(tmp_path)


async def test_failed_migration_rolls_back_and_is_not_recorded(db, tmp_path):
    shutil.copy(migrate.MIGRATIONS_DIR / "0001_init.sql", tmp_path / "0001_init.sql")
    (tmp_path / "0002_bad.sql").write_text(
        "CREATE TABLE tam_thoi(x int); SELECT 1/0;", encoding="utf-8")
    with pytest.raises(Exception):
        await migrate.run(_dsn(), tmp_path)
    async with db.connection() as conn:
        assert await conn.fetchval("SELECT to_regclass('public.tam_thoi')") is None
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations WHERE version='0002_bad'") == 0


async def test_constraints_reject_bad_data(db):
    import asyncpg
    async with db.connection() as conn:
        with pytest.raises(asyncpg.CheckViolationError):  # email phải viết thường
            await conn.execute("INSERT INTO accounts(id,username,email,password_hash,invite_key) "
                               "VALUES('a','u','Upper@x.com','h','k')")
        with pytest.raises(asyncpg.CheckViolationError):  # used_count > max_uses
            await conn.execute("INSERT INTO invite_keys(key,max_uses,used_count) VALUES('K',1,2)")
        with pytest.raises(asyncpg.CheckViolationError):  # role lạ
            await conn.execute("INSERT INTO accounts(id,username,email,password_hash,invite_key,role) "
                               "VALUES('b','u2','b@x.com','h','k','root')")
