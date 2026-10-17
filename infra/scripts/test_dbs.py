#!/usr/bin/env python3
"""Tạo database RIÊNG cho test/E2E: `<service>_db<DEV_PG_DB_SUFFIX>` (mặc định hậu tố `_test`), chủ sở hữu là user của service.

    python infra/scripts/test_dbs.py                 # tạo identity_db_test, integration_db_test, analytics_db_test, reward_db_test
    DEV_PG_DB_SUFFIX=_test python infra/scripts/test_dbs.py --check     # chỉ báo thiếu/đủ
    DEV_PG_DB_SUFFIX=_test python infra/scripts/test_dbs.py && DEV_PG_DB_SUFFIX=_test python infra/scripts/test_all.py

Vì sao: bộ test TRUNCATE dữ liệu và giữ khoá bảng; chạy trên `*_db` của stack DEV đang chạy thì (1) xoá dữ liệu DEV,
(2) deadlock/timeout với tiến trình của stack. Script CHỈ TẠO (idempotent), KHÔNG BAO GIỜ xoá database/bảng/schema.
"""

from __future__ import annotations

import asyncio
import os
import sys

import devenv

SERVICES = ("identity", "integration", "analytics", "reward")


async def main(check_only: bool) -> int:
    import asyncpg

    suffix = os.environ.get("DEV_PG_DB_SUFFIX") or "_test"
    os.environ["DEV_PG_DB_SUFFIX"] = suffix
    if not devenv.pg_ready():
        print("Postgres DEV chưa chạy: " + devenv.SKIP_REASON)
        return 1
    conn = await asyncpg.connect(devenv.pg_dsn(admin=True))
    missing = 0
    try:
        for svc in SERVICES:
            name = f"{svc}_db{suffix}"
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
            if exists:
                print(f"  có sẵn   {name}")
            elif check_only:
                print(f"  THIẾU    {name}")
                missing += 1
            else:
                await conn.execute(f'CREATE DATABASE "{name}" OWNER "{svc}_svc"')
                print(f"  đã tạo   {name}")
    finally:
        await conn.close()
    if missing:
        return 1
    print(f"\nChạy test trên các DB này:  DEV_PG_DB_SUFFIX={suffix} python infra/scripts/test_all.py")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv)))
