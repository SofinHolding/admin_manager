"""Fixture pytest cho reward-service — chạy trên Postgres THẬT (container test).

Copy nguyên văn DDL `accounts`/`invite_keys`/`refresh_tokens` từ `admin_manager/admin-server/db.py`
(`_PG_DDL`) để dựng fixture cho TEST — KHÔNG import code admin_manager, KHÔNG sửa admin_manager.
Mỗi phiên test: áp DDL admin_manager (nếu chưa có) → chạy `store.migrate.run()` → chèn account mẫu
(1 admin + 2 viewer) → ký JWT test bằng `pyjwt` với `JWT_SECRET` giống hệt env. Sau mỗi test: dọn sạch
dữ liệu account/reward_role_audit đã chèn (không DROP bảng, không đụng dữ liệu khác nếu DB được tái sử
dụng).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import time
import uuid

import asyncpg
import jwt
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from store import migrate as store_migrate
from store.pool import Pool

# Ưu tiên: REWARD_TEST_* (đè riêng cho test) → biến chuẩn (CI set DATABASE_URL/JWT_SECRET/
# ADMIN_API_BASE trực tiếp, xem .github/workflows/deploy-reward.yml) → fallback Postgres dev cục bộ.
# BUG đã vá: trước đây conftest chỉ nhìn REWARD_TEST_DATABASE_URL, bỏ qua DATABASE_URL CI đặt sẵn
# (cổng 5432 trong service Postgres của Actions) → luôn rơi về fallback cổng 5544 (Docker dev máy
# tác giả) → CI báo ConnectionRefusedError vì cổng 5544 không tồn tại trên runner GitHub.
TEST_DATABASE_URL = (os.environ.get("REWARD_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
                      or "postgres://postgres:postgres@127.0.0.1:5544/ufsync_dev")
TEST_JWT_SECRET = (os.environ.get("REWARD_TEST_JWT_SECRET") or os.environ.get("JWT_SECRET")
                    or "dev-secret-thay-doi-khi-deploy")
TEST_ADMIN_API_BASE = (os.environ.get("REWARD_TEST_ADMIN_API_BASE") or os.environ.get("ADMIN_API_BASE")
                        or "http://127.0.0.1:8421")

# Nguyên văn `admin_manager/admin-server/db.py::_PG_DDL` — fixture DÀNH RIÊNG CHO TEST, không phải sửa
# admin_manager. Nếu DDL thật đổi mà quên đồng bộ ở đây, test migrate/idempotent vẫn tự phát hiện lệch
# qua bước so sánh `information_schema` trước/sau.
ADMIN_PG_DDL = """
CREATE TABLE IF NOT EXISTS invite_keys (
    key         text PRIMARY KEY,
    role        text NOT NULL DEFAULT 'viewer',
    label       text NOT NULL DEFAULT '',
    max_uses    integer NOT NULL DEFAULT 1,
    used_count  integer NOT NULL DEFAULT 0,
    created_at  text NOT NULL,
    expires_at  text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS accounts (
    id              text PRIMARY KEY,
    username        text NOT NULL UNIQUE,
    email           text NOT NULL UNIQUE,
    password_hash   text NOT NULL,
    role            text NOT NULL DEFAULT 'viewer',
    invite_key      text NOT NULL,
    email_verified  boolean NOT NULL DEFAULT false,
    verify_token    text NOT NULL DEFAULT '',
    verify_token_at text NOT NULL DEFAULT '',
    reset_token     text NOT NULL DEFAULT '',
    reset_token_at  text NOT NULL DEFAULT '',
    created_at      text NOT NULL,
    last_login      text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token       text PRIMARY KEY,
    account_id  text NOT NULL,
    created_at  text NOT NULL,
    expires_at  text NOT NULL,
    revoked     boolean NOT NULL DEFAULT false
);
"""


def _set_test_env() -> None:
    os.environ["JWT_SECRET"] = TEST_JWT_SECRET
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["ADMIN_API_BASE"] = TEST_ADMIN_API_BASE
    os.environ.setdefault("REWARD_TOKEN_KEY", Fernet.generate_key().decode())
    os.environ["REWARD_PORT"] = "8422"
    os.environ["REWARD_DB_MIGRATE_ON_BOOT"] = "1"
    os.environ["REWARD_CORS_ORIGINS"] = "http://localhost:5175"


_set_test_env()


@pytest_asyncio.fixture
async def db_conn():
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    await conn.execute(ADMIN_PG_DDL)
    try:
        yield conn
    finally:
        # Dọn dữ liệu reward_* + account/audit test tạo ra — KHÔNG DROP bảng (bảng thuộc "hạ tầng"
        # fixture dùng chung). Thứ tự: distributions trước items (RESTRICT), rồi jobs (CASCADE
        # items/attempts), rồi phần còn lại.
        await conn.execute("DELETE FROM reward_distributions")
        await conn.execute("DELETE FROM reward_job_events")
        await conn.execute("DELETE FROM reward_runner_lock")
        await conn.execute("DELETE FROM reward_user_map")
        await conn.execute("DELETE FROM reward_role_audit")
        await conn.execute("DELETE FROM reward_jobs")
        await conn.execute("DELETE FROM reward_discord_credentials")
        await conn.execute("DELETE FROM accounts")
        await conn.close()


def make_account_row(*, role: str, status: str = "active", username: str | None = None) -> dict:
    account_id = uuid.uuid4().hex
    username = username or f"user_{account_id[:8]}"
    return {
        "id": account_id,
        "username": username,
        "email": f"{username}@example.test",
        "password_hash": "x",
        "role": role,
        "invite_key": "TEST-0000",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": status,
    }


async def insert_account(conn: asyncpg.Connection, **kwargs) -> dict:
    row = make_account_row(**kwargs)
    await conn.execute(
        "INSERT INTO accounts (id, username, email, password_hash, role, invite_key, created_at, status) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        row["id"], row["username"], row["email"], row["password_hash"], row["role"],
        row["invite_key"], row["created_at"], row["status"])
    return row



async def seed_active_account_with_credential(
    conn: asyncpg.Connection, *, role: str = "discord", credential_status: str = "valid",
) -> str:
    """Chèn 1 account active + 1 `reward_discord_credentials` `status='valid'` — dùng cho test gọi
    `JobRunner.run()`/`WorkerManager.start()` trực tiếp (bắt buộc từ khi thêm kiểm T2 mỗi vòng lặp,
    C.5 #2). Trả `account_id`."""
    row = await insert_account(conn, role=role, status="active")
    await conn.execute(
        "INSERT INTO reward_discord_credentials (account_id, token_ciphertext, status) "
        "VALUES ($1, 'test-ciphertext', $2)",
        row["id"], credential_status,
    )
    return row["id"]

def sign_token(account: dict, *, secret: str = TEST_JWT_SECRET, ttl_s: int = 900, role: str | None = None) -> str:
    """Ký JWT y hệt payload `admin_manager/admin-server/auth.py:_create_access_token`."""
    now = int(time.time())
    payload = {
        "sub": account["id"],
        "username": account["username"],
        "role": role if role is not None else account["role"],
        "exp": now + ttl_s,
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def bearer():
    def _bearer(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}
    return _bearer


@pytest_asyncio.fixture
async def pool(db_conn):
    """`Pool` đã mở + migrate — dùng cho test engine (`job_runner`, `recovery`, repositories).

    Phụ thuộc `db_conn` để đảm bảo DDL admin_manager tồn tại VÀ để tái sử dụng logic dọn dẹp chung
    (fixture `db_conn` dọn `reward_*`/`accounts` sau khi `pool` đã đóng, vì Python teardown chạy theo
    thứ tự ngược của yêu cầu fixture)."""
    p = Pool(TEST_DATABASE_URL)
    await p.open()
    await store_migrate.run(p)
    try:
        yield p
    finally:
        await p.close()


async def seed_job(
    conn: asyncpg.Connection, *, account_id: str, rows: list[dict],
    channel_id: str = "C", guild_id: str = "G", command_name: str = "give-xp",
    confirm_mode: str = "reply", success_pattern: str | None = "has been given",
    failure_pattern: str | None = None, leveling_bot_id: str | None = None,
    delay_ms: int = 0, jitter_ms: int = 0, max_item_retries: int = 3,
    unknown_pause_threshold: int = 5, status: str = "validated",
) -> int:
    """Tạo 1 job + N item trực tiếp trong DB (thay cho API job chưa có ở P2 — xem D.5/Contract).

    `rows`: `[{"raw_username":..., "point":...}, ...]` — `normalized_username`/`idempotency_key` tự
    sinh bằng `domain.parser`/`domain.idempotency` (đúng công thức B.2.3)."""
    from domain.idempotency import item_key
    from domain.parser import normalize_username

    job_id = await conn.fetchrow(
        """
        INSERT INTO reward_jobs
            (account_id, name, status, guild_id, channel_id, command_name, confirm_mode,
             success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms,
             max_item_retries, unknown_pause_threshold, total_items)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        RETURNING id
        """,
        account_id, f"job-{uuid.uuid4().hex[:8]}", status, guild_id, channel_id, command_name,
        confirm_mode, success_pattern, failure_pattern, leveling_bot_id, delay_ms, jitter_ms,
        max_item_retries, unknown_pause_threshold, len(rows),
    )
    job_id = job_id["id"]
    for i, r in enumerate(rows):
        normalized = normalize_username(r["raw_username"])
        key = item_key(job_id=job_id, row_index=i, normalized_username=normalized, point=r["point"])
        await conn.execute(
            "INSERT INTO reward_items "
            "(job_id, row_index, raw_username, normalized_username, point, status, idempotency_key) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            job_id, i, r["raw_username"], normalized, r["point"], r.get("status", "pending"), key,
        )
    return job_id
