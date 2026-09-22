"""Test verify JWT cục bộ + hai tầng phân quyền T1 (token) / T2 (DB) của `security.py`."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from security import Security, decode_access_token  # noqa: E402
from settings import load_settings  # noqa: E402
from store.pool import Pool  # noqa: E402
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET, insert_account, sign_token  # noqa: E402


def test_decode_access_token_valid():
    now = int(time.time())
    token = jwt.encode(
        {"sub": "abc", "username": "bob", "role": "admin", "exp": now + 900, "iat": now},
        TEST_JWT_SECRET, algorithm="HS256")
    payload = decode_access_token(token, TEST_JWT_SECRET)
    assert payload["sub"] == "abc"
    assert payload["role"] == "admin"


def test_decode_access_token_expired():
    now = int(time.time())
    token = jwt.encode(
        {"sub": "abc", "username": "bob", "role": "admin", "exp": now - 10, "iat": now - 100},
        TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token, TEST_JWT_SECRET)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token hết hạn"


def test_decode_access_token_wrong_secret():
    now = int(time.time())
    token = jwt.encode(
        {"sub": "abc", "username": "bob", "role": "admin", "exp": now + 900, "iat": now},
        "a-completely-different-secret", algorithm="HS256")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token, TEST_JWT_SECRET)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token không hợp lệ"


async def test_require_reward_user_rejects_viewer_role(db_conn):
    settings = load_settings()
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    security = Security(settings, pool)
    try:
        account = await insert_account(db_conn, role="viewer")
        token = sign_token(account)
        with pytest.raises(HTTPException) as exc_info:
            await security.require_reward_user(f"Bearer {token}")
        assert exc_info.value.status_code == 403
    finally:
        await pool.close()


async def test_require_active_account_rejects_suspended(db_conn):
    settings = load_settings()
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    security = Security(settings, pool)
    try:
        account = await insert_account(db_conn, role="admin", status="suspended")
        token = sign_token(account)
        with pytest.raises(HTTPException) as exc_info:
            await security.require_active_account(f"Bearer {token}")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Tài khoản không hoạt động"
    finally:
        await pool.close()


async def test_require_active_account_rejects_demoted_admin(db_conn):
    """Token còn claim role='admin' (ký lúc chưa demote) nhưng DB hiện tại đã hạ xuống 'viewer' -> 403."""
    settings = load_settings()
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    security = Security(settings, pool)
    try:
        account = await insert_account(db_conn, role="viewer")
        token = sign_token(account, role="admin")  # token cũ vẫn claim admin
        with pytest.raises(HTTPException) as exc_info:
            await security.require_active_account(f"Bearer {token}")
        assert exc_info.value.status_code == 403
    finally:
        await pool.close()


async def test_require_active_account_accepts_matching_role(db_conn):
    settings = load_settings()
    pool = Pool(TEST_DATABASE_URL)
    await pool.open()
    security = Security(settings, pool)
    try:
        account = await insert_account(db_conn, role="discord")
        token = sign_token(account)
        ctx = await security.require_active_account(f"Bearer {token}")
        assert ctx.account_id == account["id"]
        assert ctx.token_role == "discord"
    finally:
        await pool.close()


def test_missing_authorization_header_is_401():
    with pytest.raises(HTTPException) as exc_info:
        from security import extract_token
        extract_token("")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Thiếu token"
