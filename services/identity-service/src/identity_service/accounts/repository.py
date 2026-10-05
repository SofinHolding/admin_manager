"""SQL cho bảng `accounts` (và chỉ bảng đó). Mọi hàm nhận `conn` để gộp vào transaction của service."""

from __future__ import annotations

from datetime import datetime

import asyncpg

_PROFILE_COLUMNS = {"username", "email"}
_ADMIN_COLUMNS = {"username", "email", "role"}


def _row(record: asyncpg.Record | None) -> dict | None:
    return dict(record) if record else None


async def get_by_id(conn: asyncpg.Connection, account_id: str) -> dict | None:
    return _row(await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id))


async def get_by_username(conn: asyncpg.Connection, username: str) -> dict | None:
    return _row(await conn.fetchrow("SELECT * FROM accounts WHERE username = $1", username))


async def get_by_email(conn: asyncpg.Connection, email: str) -> dict | None:
    return _row(await conn.fetchrow("SELECT * FROM accounts WHERE email = $1", email.lower()))


async def username_exists(conn: asyncpg.Connection, username: str) -> bool:
    return await conn.fetchval("SELECT EXISTS(SELECT 1 FROM accounts WHERE username = $1)", username)


async def email_exists(conn: asyncpg.Connection, email: str) -> bool:
    return await conn.fetchval("SELECT EXISTS(SELECT 1 FROM accounts WHERE email = $1)", email.lower())


async def create(conn: asyncpg.Connection, *, account_id: str, username: str, email: str,
                 password_hash: str, role: str, invite_key: str, verify_token_hash: str,
                 now: datetime) -> dict:
    """Ném asyncpg.UniqueViolationError nếu trùng username/email (service dịch thành 409)."""
    row = await conn.fetchrow(
        "INSERT INTO accounts (id, username, email, password_hash, role, invite_key, "
        "verify_token_hash, verify_token_at, created_at, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, 'pending') RETURNING *",
        account_id, username, email.lower(), password_hash, role, invite_key, verify_token_hash, now)
    return dict(row)


async def get_by_verify_hash(conn: asyncpg.Connection, token_hash: str) -> dict | None:
    if not token_hash:
        return None
    return _row(await conn.fetchrow(
        "SELECT * FROM accounts WHERE verify_token_hash = $1 AND verify_token_hash <> ''", token_hash))


async def set_verify_token(conn: asyncpg.Connection, account_id: str, token_hash: str,
                           now: datetime) -> None:
    await conn.execute("UPDATE accounts SET verify_token_hash = $2, verify_token_at = $3 WHERE id = $1",
                       account_id, token_hash, now)


async def mark_verified(conn: asyncpg.Connection, account_id: str) -> None:
    """Chỉ chuyển pending -> active. Tài khoản đang bị khóa (suspended) KHÔNG được mở lại bằng link xác thực
    (legacy đặt status='active' vô điều kiện nên một link cũ có thể gỡ khóa của admin)."""
    await conn.execute(
        "UPDATE accounts SET email_verified = true, verify_token_hash = '', verify_token_at = NULL, "
        "status = CASE WHEN status = 'pending' THEN 'active' ELSE status END WHERE id = $1", account_id)


async def set_reset_token(conn: asyncpg.Connection, account_id: str, token_hash: str,
                          now: datetime) -> None:
    await conn.execute(
        "UPDATE accounts SET reset_token_hash = $2, reset_token_at = $3 WHERE id = $1",
        account_id, token_hash, now)


async def get_by_reset_hash(conn: asyncpg.Connection, token_hash: str) -> dict | None:
    if not token_hash:
        return None
    return _row(await conn.fetchrow(
        "SELECT * FROM accounts WHERE reset_token_hash = $1 AND reset_token_hash <> ''", token_hash))


async def clear_reset_token(conn: asyncpg.Connection, account_id: str) -> None:
    await conn.execute(
        "UPDATE accounts SET reset_token_hash = '', reset_token_at = NULL WHERE id = $1", account_id)


async def set_change_code(conn: asyncpg.Connection, account_id: str, code_hash: str,
                          now: datetime) -> None:
    await conn.execute(
        "UPDATE accounts SET change_code_hash = $2, change_code_at = $3, change_code_attempts = 0 "
        "WHERE id = $1", account_id, code_hash, now)


async def clear_change_code(conn: asyncpg.Connection, account_id: str) -> None:
    await conn.execute(
        "UPDATE accounts SET change_code_hash = '', change_code_at = NULL, change_code_attempts = 0 "
        "WHERE id = $1", account_id)


async def consume_change_code(conn: asyncpg.Connection, account_id: str, code_hash: str) -> bool:
    """Dùng mã ATOMIC (chỉ thành công nếu mã vẫn còn nguyên): hai request song song không cùng dùng được."""
    row = await conn.fetchval(
        "UPDATE accounts SET change_code_hash = '', change_code_at = NULL, change_code_attempts = 0 "
        "WHERE id = $1 AND change_code_hash = $2 RETURNING 1", account_id, code_hash)
    return row == 1


async def bump_change_attempts(conn: asyncpg.Connection, account_id: str) -> int:
    return await conn.fetchval(
        "UPDATE accounts SET change_code_attempts = change_code_attempts + 1 WHERE id = $1 "
        "RETURNING change_code_attempts", account_id)


async def update_password(conn: asyncpg.Connection, account_id: str, password_hash: str) -> None:
    await conn.execute("UPDATE accounts SET password_hash = $2 WHERE id = $1", account_id, password_hash)


async def update_last_login(conn: asyncpg.Connection, account_id: str, now: datetime) -> None:
    await conn.execute("UPDATE accounts SET last_login = $2 WHERE id = $1", account_id, now)


async def list_all(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, username, email, role, invite_key, email_verified, created_at, last_login, status "
        "FROM accounts ORDER BY created_at DESC, id")
    return [dict(r) for r in rows]


async def set_status(conn: asyncpg.Connection, account_id: str, new_status: str) -> bool:
    tag = await conn.execute("UPDATE accounts SET status = $2 WHERE id = $1", account_id, new_status)
    return tag.endswith(" 1")


async def delete(conn: asyncpg.Connection, account_id: str) -> bool:
    """Xóa tài khoản; refresh_tokens bị xóa theo (FK ON DELETE CASCADE)."""
    tag = await conn.execute("DELETE FROM accounts WHERE id = $1", account_id)
    return tag.endswith(" 1")


async def _update_columns(conn: asyncpg.Connection, account_id: str, updates: dict,
                          allowed: set[str]) -> bool:
    items = [(k, v) for k, v in updates.items() if k in allowed]
    if not items:
        return True
    sets = ", ".join(f"{k} = ${i + 1}" for i, (k, _) in enumerate(items))  # tên cột lấy từ whitelist
    tag = await conn.execute(f"UPDATE accounts SET {sets} WHERE id = ${len(items) + 1}",
                             *[v for _, v in items], account_id)
    return tag.endswith(" 1")


async def update_profile(conn: asyncpg.Connection, account_id: str, updates: dict) -> bool:
    return await _update_columns(conn, account_id, updates, _PROFILE_COLUMNS)


async def update_by_admin(conn: asyncpg.Connection, account_id: str, updates: dict) -> bool:
    return await _update_columns(conn, account_id, updates, _ADMIN_COLUMNS)
