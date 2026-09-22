"""Chạy migration SQL trong `store/migrations/` — mỗi file một transaction, idempotent.

Ghi version đã áp vào `reward_schema_migrations`. Toàn bộ DDL trong các file migration là
`CREATE ... IF NOT EXISTS` — không một câu `ALTER`/`DROP` nào chạm bảng của admin_manager.
"""

from __future__ import annotations

import logging
from pathlib import Path

from store.pool import Pool

logger = logging.getLogger("reward.migrate")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _load_migrations() -> list[tuple[str, str]]:
    """Trả về `[(version, sql)]` theo thứ tự tên file — `version` = tên file bỏ `.sql`."""
    return [(path.stem, path.read_text(encoding="utf-8")) for path in sorted(_MIGRATIONS_DIR.glob("*.sql"))]


async def run(pool: Pool) -> str:
    """Áp mọi migration chưa chạy, mỗi file trong MỘT transaction. Idempotent.

    Trả về version mới nhất tìm thấy trên đĩa (không nhất thiết vừa áp — có thể đã áp từ trước)."""
    migrations = _load_migrations()
    if not migrations:
        raise RuntimeError("Không có file migration nào trong store/migrations/")

    async with pool.acquire() as conn:
        table_exists = await conn.fetchval("SELECT to_regclass('public.reward_schema_migrations') IS NOT NULL")
        applied: set[str] = set()
        if table_exists:
            rows = await conn.fetch("SELECT version FROM reward_schema_migrations")
            applied = {row["version"] for row in rows}

        for version, sql in migrations:
            if version in applied:
                logger.info("Bỏ qua migration đã áp dụng: %s", version)
                continue
            logger.info("Áp dụng migration: %s", version)
            async with conn.transaction():
                await conn.execute(sql)

    return migrations[-1][0]


async def current_version(pool: Pool) -> str | None:
    """Đọc version mới nhất đã áp trong DB — dùng cho `/v1/reward/health`."""
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT version FROM reward_schema_migrations ORDER BY applied_at DESC LIMIT 1")
    except Exception:  # noqa: BLE001 — bảng có thể chưa tồn tại (chưa migrate) hoặc DB down
        return None


if __name__ == "__main__":  # `python -m store.migrate` — chạy migrate một lần (deploy/CI).
    import asyncio
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from settings import load_settings  # noqa: E402

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    async def _main() -> None:
        settings = load_settings()
        pool = Pool(settings.database_url)
        await pool.open()
        try:
            version = await run(pool)
            logger.info("Migrate xong — version mới nhất: %s", version)
        finally:
            await pool.close()

    asyncio.run(_main())
