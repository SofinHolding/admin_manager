"""Platform Registry và Metric Registry — DỮ LIỆU (seed bằng migration), thêm dòng không cần đổi code."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def enabled_platforms(conn: asyncpg.Connection) -> set[str]:
    return {r["key"] for r in await conn.fetch("SELECT key FROM platform_registry WHERE enabled")}


async def enabled_metrics(conn: asyncpg.Connection) -> set[str]:
    return {r["key"] for r in await conn.fetch("SELECT key FROM metric_registry WHERE enabled")}


async def list_platforms(conn: asyncpg.Connection) -> list[dict]:
    """Nền tảng kèm `aliases` (mã phụ, sắp xếp)."""
    rows = await conn.fetch(
        "SELECT r.*, COALESCE(array_agg(a.alias ORDER BY a.alias) FILTER (WHERE a.alias IS NOT NULL), '{}') AS aliases "
        "FROM platform_registry r LEFT JOIN platform_aliases a ON a.platform_key = r.key GROUP BY r.key ORDER BY r.key")
    return [dict(r) for r in rows]


# ── Mã nền tảng: mã chính (`key`) + mã phụ (`aliases`) — duy nhất toàn hệ thống ───────────────────────────────────

async def code_map(conn: asyncpg.Connection) -> dict[str, str]:
    """Mọi mã (chính + phụ) -> mã chính của nền tảng. Mã chính trỏ về chính nó."""
    out = {r["key"]: r["key"] for r in await conn.fetch("SELECT key FROM platform_registry")}
    out.update({r["alias"]: r["platform_key"] for r in await conn.fetch("SELECT alias, platform_key FROM platform_aliases")})
    return out


async def resolve(conn: asyncpg.Connection, code: str) -> str:
    """Chuẩn hoá một mã Tool gửi về mã chính. Mã lạ giữ nguyên (các bước sau sẽ từ chối vì không có trong registry)."""
    return await code_owner(conn, code) or code


async def resolve_many(conn: asyncpg.Connection, codes: list[str]) -> list[str]:
    """Chuẩn hoá danh sách mã, bỏ trùng, giữ thứ tự xuất hiện."""
    mapping = await code_map(conn)
    return list(dict.fromkeys(mapping.get(c, c) for c in codes))


async def lock_codes(conn: asyncpg.Connection) -> None:
    """Khoá giao dịch cho thao tác tạo/đổi mã: hai request song song không thể cùng giành một mã."""
    await conn.execute("SELECT pg_advisory_xact_lock(7788001)")


async def code_owner(conn: asyncpg.Connection, code: str) -> str | None:
    """Nền tảng đang giữ mã này (là mã chính hoặc mã phụ); None nếu mã còn trống."""
    row = await conn.fetchrow(
        "SELECT key AS k FROM platform_registry WHERE key = $1 UNION ALL "
        "SELECT platform_key FROM platform_aliases WHERE alias = $1 LIMIT 1", code)
    return row["k"] if row else None


async def insert_platform(conn: asyncpg.Connection, key: str, display_name: str, now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO platform_registry (key, display_name, enabled, created_at, updated_at) "
        "VALUES ($1, $2, true, $3, $3) RETURNING *", key, display_name, now)
    return dict(row)


async def insert_alias(conn: asyncpg.Connection, key: str, alias: str, now: datetime) -> None:
    await conn.execute("INSERT INTO platform_aliases (alias, platform_key, created_at) VALUES ($1, $2, $3)", alias, key, now)


async def delete_alias(conn: asyncpg.Connection, key: str, alias: str) -> bool:
    res = await conn.execute("DELETE FROM platform_aliases WHERE alias = $1 AND platform_key = $2", alias, key)
    return res.endswith(" 1")


async def get_platform(conn: asyncpg.Connection, key: str) -> dict | None:
    return next((p for p in await list_platforms(conn) if p["key"] == key), None)


async def list_metrics(conn: asyncpg.Connection) -> list[dict]:
    return [dict(r) for r in await conn.fetch("SELECT * FROM metric_registry ORDER BY key")]


async def upsert_platform(conn: asyncpg.Connection, key: str, display_name: str, enabled: bool, now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO platform_registry (key, display_name, enabled, created_at, updated_at) "
        "VALUES ($1, $2, $3, $4, $4) ON CONFLICT (key) DO UPDATE SET display_name = EXCLUDED.display_name, "
        "enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at RETURNING *", key, display_name, enabled, now)
    return dict(row)


async def grant_to_personal_clients(conn: asyncpg.Connection, key: str, now: datetime) -> int:
    """Tool CÁ NHÂN (primary_tool) được provision với MỌI nền tảng đang bật (`provisioning/handlers.py`); nền tảng Admin tạo
    sau đó phải tự có trong danh sách đó, nếu không Tool cá nhân không gửi được. Tool DÙNG CHUNG thì KHÔNG: admin chọn
    từng nền tảng khi cấu hình client. Trả số client được thêm."""
    res = await conn.execute(
        "UPDATE integration_clients SET allowed_platforms = array_append(allowed_platforms, $1), updated_at = $2 "
        "WHERE client_kind = 'primary_tool' AND NOT ($1 = ANY(allowed_platforms))", key, now)
    return int(res.rsplit(" ", 1)[-1])


async def upsert_metric(conn: asyncpg.Connection, key: str, display_name: str, description: str, enabled: bool,
                        now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO metric_registry (key, display_name, description, enabled, created_at, updated_at) "
        "VALUES ($1, $2, $3, $4, $5, $5) ON CONFLICT (key) DO UPDATE SET display_name = EXCLUDED.display_name, "
        "description = EXCLUDED.description, enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at "
        "RETURNING *", key, display_name, description, enabled, now)
    return dict(row)
