"""Kết nối PostgreSQL (asyncpg) tới `analytics_db` — DB RIÊNG của service này, user riêng.

Repository nhận `conn` (đã nằm trong transaction nếu cần) nên nhiều thao tác có thể gộp atomic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import json

import asyncpg


async def _init_connection(conn: asyncpg.Connection) -> None:
    """jsonb <-> đối tượng Python (asyncpg mặc định trả chuỗi)."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self, dsn: str, *, min_size: int = 0, max_size: int = 10,
                 connect_timeout: float = 3.0, acquire_timeout: float = 5.0) -> None:
        # Timeout NGẮN có chủ đích: DB chết thì request phải thất bại nhanh (503) chứ không treo 60s
        # (mặc định của asyncpg), nếu không worker bị giữ lại và lỗi lan sang các request khác.
        self._dsn = dsn
        self._min = min_size
        self._max = max_size
        self._connect_timeout = connect_timeout
        self._acquire_timeout = acquire_timeout
        self._pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=self._min, max_size=self._max,
                                                   command_timeout=15, timeout=self._connect_timeout,
                                                   init=_init_connection)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database chưa mở (gọi open() trước)")
        return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        await self.open()  # idempotent: pool lười, tự mở khi dùng lần đầu
        async with self.pool.acquire(timeout=self._acquire_timeout) as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """Một connection trong một transaction: commit khi thoát bình thường, rollback khi có lỗi."""
        await self.open()
        async with self.pool.acquire(timeout=self._acquire_timeout) as conn:
            async with conn.transaction():
                yield conn

    async def ping(self) -> bool:
        try:
            async with self.connection() as conn:
                return await conn.fetchval("SELECT 1") == 1
        except Exception:  # noqa: BLE001 — DB hỏng = chưa sẵn sàng, không được ném ra ngoài health
            return False
