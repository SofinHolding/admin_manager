"""SQL nhóm kênh. Nhận `conn`; mọi hàm ghi phải được gọi trong transaction cùng `bump_seal`."""

from __future__ import annotations

import uuid

import asyncpg

from analytics_service.dates import iso_utc


def _group(row) -> dict:
    return {"id": row["id"], "account_id": row["account_id"], "name": row["name"],
            "created_at": iso_utc(row["created_at"]), "updated_at": iso_utc(row["updated_at"])}


async def create_group(conn: asyncpg.Connection, account_id: str, name: str) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO channel_groups (id, account_id, name) VALUES ($1, $2, $3)"
        " RETURNING id, account_id, name, created_at, updated_at", uuid.uuid4().hex, account_id, name)
    return _group(row)


async def get_group(conn: asyncpg.Connection, group_id: str) -> dict | None:
    row = await conn.fetchrow("SELECT id, account_id, name, created_at, updated_at FROM channel_groups WHERE id = $1",
                              group_id)
    return _group(row) if row else None


async def list_groups(conn: asyncpg.Connection, account_id: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, account_id, name, created_at, updated_at FROM channel_groups WHERE account_id = $1"
        " ORDER BY created_at DESC, id", account_id)
    groups = [_group(r) for r in rows]
    members: dict[str, list[dict]] = {g["id"]: [] for g in groups}
    if groups:
        for m in await conn.fetch(
                "SELECT group_id, channel_ref, platform_tag, country_override FROM channel_group_members"
                " WHERE group_id = ANY($1::text[]) ORDER BY created_at, channel_ref", list(members)):
            members[m["group_id"]].append({"channel_ref": m["channel_ref"], "platform_tag": m["platform_tag"],
                                           "country_override": m["country_override"]})
    for g in groups:
        g["members"] = members[g["id"]]
    return groups


async def rename_group(conn: asyncpg.Connection, group_id: str, name: str) -> None:
    await conn.execute("UPDATE channel_groups SET name = $2, updated_at = now() WHERE id = $1", group_id, name)


async def delete_group(conn: asyncpg.Connection, group_id: str) -> None:
    await conn.execute("DELETE FROM channel_groups WHERE id = $1", group_id)   # thành viên xoá theo (ON DELETE CASCADE)


async def upsert_member(conn: asyncpg.Connection, group_id: str, channel_ref: str, platform_tag: str,
                        country_override: str) -> dict:
    await conn.execute(
        "INSERT INTO channel_group_members (group_id, channel_ref, platform_tag, country_override)"
        " VALUES ($1, $2, $3, $4) ON CONFLICT (group_id, channel_ref) DO UPDATE SET"
        " platform_tag = EXCLUDED.platform_tag, country_override = EXCLUDED.country_override",
        group_id, channel_ref, platform_tag, country_override)
    await conn.execute("UPDATE channel_groups SET updated_at = now() WHERE id = $1", group_id)
    return {"group_id": group_id, "channel_ref": channel_ref, "platform_tag": platform_tag,
            "country_override": country_override}


async def remove_member(conn: asyncpg.Connection, group_id: str, channel_ref: str) -> bool:
    tag = await conn.execute("DELETE FROM channel_group_members WHERE group_id = $1 AND channel_ref = $2",
                             group_id, channel_ref)
    return int(tag.rsplit(" ", 1)[-1]) > 0
