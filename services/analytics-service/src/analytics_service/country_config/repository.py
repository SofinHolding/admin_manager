"""SQL alias / tên chuẩn quốc gia. Nhận `conn`."""

from __future__ import annotations

import asyncpg

from analytics_service.dates import iso_utc


def _alias(r) -> dict:
    return {"alias": r["alias"], "canonical": r["canonical"], "created_at": iso_utc(r["created_at"])}


async def list_aliases(conn: asyncpg.Connection) -> list[dict]:
    return [_alias(r) for r in await conn.fetch(
        "SELECT alias, canonical, created_at FROM country_aliases ORDER BY canonical, alias")]


async def upsert_alias(conn: asyncpg.Connection, alias: str, canonical: str) -> dict:
    key = alias.strip().lower()
    await conn.execute(
        "INSERT INTO country_aliases (alias, canonical) VALUES ($1, $2)"
        " ON CONFLICT (alias) DO UPDATE SET canonical = EXCLUDED.canonical", key, canonical.strip())
    return {"alias": key, "canonical": canonical.strip()}


async def delete_alias(conn: asyncpg.Connection, alias: str) -> bool:
    tag = await conn.execute("DELETE FROM country_aliases WHERE alias = $1", alias.strip().lower())
    return int(tag.rsplit(" ", 1)[-1]) > 0


async def list_canonicals(conn: asyncpg.Connection) -> list[dict]:
    return [{"name": r["name"], "created_at": iso_utc(r["created_at"])} for r in await conn.fetch(
        "SELECT name, created_at FROM country_canonicals ORDER BY name")]


async def get_canonical(conn: asyncpg.Connection, name: str) -> dict | None:
    r = await conn.fetchrow("SELECT name, created_at FROM country_canonicals WHERE lower(name) = lower($1)",
                            name.strip())
    return {"name": r["name"], "created_at": iso_utc(r["created_at"])} if r else None


async def create_canonical(conn: asyncpg.Connection, name: str) -> dict:
    r = await conn.fetchrow("INSERT INTO country_canonicals (name) VALUES ($1) RETURNING name, created_at", name.strip())
    return {"name": r["name"], "created_at": iso_utc(r["created_at"])}


async def rename_canonical(conn: asyncpg.Connection, old: str, new: str) -> None:
    await conn.execute("UPDATE country_canonicals SET name = $2 WHERE lower(name) = lower($1)", old.strip(), new.strip())
    await conn.execute("UPDATE country_aliases SET canonical = $2 WHERE lower(canonical) = lower($1)",
                       old.strip(), new.strip())


async def delete_canonical(conn: asyncpg.Connection, name: str) -> None:
    await conn.execute("DELETE FROM country_canonicals WHERE lower(name) = lower($1)", name.strip())


async def count_aliases_for(conn: asyncpg.Connection, canonical: str) -> int:
    return int(await conn.fetchval("SELECT count(*) FROM country_aliases WHERE lower(canonical) = lower($1)", canonical))
