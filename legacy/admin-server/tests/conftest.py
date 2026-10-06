"""Fixture pytest cho admin-server — SQLite tạm thời per-session, không đụng `admin-auth.db` thật.

Biến môi trường PHẢI set TRƯỚC khi import `main` (module-level `_db = CachedDb(make_db(...))` chạy
ngay lúc import — xem `main.py`)."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TEST_DB = Path(__file__).resolve().parent / "_test_inventory.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-local-ops-hub"
os.environ["MODE"] = "production"
os.environ["REDIS_URL"] = ""
os.environ["CACHE_ENABLED"] = "0"
os.environ["ADMIN_WEB_DIR"] = ""

import bcrypt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main as app_main  # noqa: E402
import auth as auth_mod  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app_main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Mỗi test có thể login nhiều lần từ cùng 1 IP testserver — xoá bộ đếm rate-limit đăng nhập
    (`auth._rate`) trước mỗi test để test không tự gây 429 lẫn nhau."""
    auth_mod._rate.clear()
    yield


async def _make_account(role: str, *, status: str = "active") -> dict:
    """Tạo account thẳng qua tầng Db (bỏ qua flow verify-email) — trả dict có `password` thô để
    test login."""
    db = app_main._db
    username = f"u{uuid.uuid4().hex[:12]}"
    password = "Test-pass-123"
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    acc = await db.create_account(username, f"{username}@test.local", pw_hash, role, "")
    await db.set_account_status(acc["id"], status)
    return {**acc, "password": password, "role": role, "status": status}


@pytest.fixture
def make_account():
    """Factory fixture — `await make_account(role="viewer")` trong test async."""
    return _make_account


def login(client: TestClient, username: str, password: str, *, client_type: str = "web") -> dict:
    r = client.post("/v1/auth/login", json={"username": username, "password": password},
                    headers={"X-Client-Type": client_type} if client_type != "web" else {})
    return r


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
