"""SQL cho bảng `invite_keys` (và chỉ bảng đó)."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta

import asyncpg

_ALPHABET = string.ascii_uppercase + string.digits


def generate_key() -> str:
    """Key ngắn dạng XXXX-XXXX (chữ in hoa + số), dễ copy-paste — cùng định dạng legacy."""
    part = lambda: "".join(secrets.choice(_ALPHABET) for _ in range(4))  # noqa: E731
    return f"{part()}-{part()}"


async def get(conn: asyncpg.Connection, key: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM invite_keys WHERE key = $1", key)
    return dict(row) if row else None


async def consume(conn: asyncpg.Connection, key: str, now: datetime) -> dict | None:
    """Dùng 1 lượt ATOMIC: kiểm tra và tăng trong cùng một câu UPDATE, kể cả điều kiện hết hạn.

    Trả về dòng key sau khi tăng, hoặc None nếu key không còn dùng được (hết lượt, bị thu hồi, hết hạn).
    Legacy không kiểm hết hạn ở bước dùng key (chỉ ở validate-key), nên key hết hạn vẫn đăng ký được.
    """
    row = await conn.fetchrow(
        "UPDATE invite_keys SET used_count = used_count + 1 "
        "WHERE key = $1 AND status = 'active' AND used_count < max_uses "
        "AND (expires_at IS NULL OR expires_at > $2) RETURNING *", key, now)
    return dict(row) if row else None


async def create(conn: asyncpg.Connection, *, role: str, label: str, max_uses: int, expires_days: int,
                 now: datetime) -> dict:
    expires = now + timedelta(days=expires_days)
    for _ in range(10):  # va chạm key gần như không xảy ra; thử lại thay vì lỗi
        key = generate_key()
        try:
            row = await conn.fetchrow(
                "INSERT INTO invite_keys (key, role, label, max_uses, created_at, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *", key, role, label, max_uses, now, expires)
            return dict(row)
        except asyncpg.UniqueViolationError:
            continue
    raise RuntimeError("Không sinh được invite key duy nhất")


async def list_all(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM invite_keys ORDER BY created_at DESC, key")
    return [dict(r) for r in rows]


async def revoke(conn: asyncpg.Connection, key: str) -> bool:
    tag = await conn.execute("UPDATE invite_keys SET status = 'revoked' WHERE key = $1", key)
    return tag.endswith(" 1")
