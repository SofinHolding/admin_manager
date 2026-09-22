"""Quản lý `asyncpg` connection pool dùng chung Postgres `ufsync` với admin_manager.

reward-service KHÔNG BAO GIỜ INSERT/DELETE/ALTER bảng của admin_manager (`accounts`, `invite_keys`,
`refresh_tokens`, `documents`, `user_meta`) — chỉ `SELECT accounts` + `UPDATE accounts.role`
(xem `store/repositories/accounts.py`). Mọi bảng khác thuộc namespace `reward_*` do service này sở hữu.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger("reward.pool")


class Pool:
    """Bọc `asyncpg.Pool` — mở trong `lifespan`, đóng khi tắt service."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
        logger.info("Đã mở Postgres pool (min_size=2, max_size=10)")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Đã đóng Postgres pool")

    def acquire(self):
        if self._pool is None:
            raise RuntimeError("Pool chưa mở — gọi open() trước khi dùng")
        return self._pool.acquire()

    async def ping(self) -> bool:
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 — health-check chỉ cần biết được/không, không cần phân loại lỗi
            logger.exception("Health-check Postgres thất bại")
            return False
