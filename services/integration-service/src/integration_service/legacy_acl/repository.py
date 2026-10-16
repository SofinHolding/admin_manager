"""SQL của Legacy ACL: schema `legacy_ingest` (Source, policy, Run Queue, pairing) và các TRUY VẤN ĐỌC trên `resources`.

GHI vào `resources` KHÔNG nằm ở đây: mọi ghi resource đi qua `ingest.merge` (cùng đường với Tool, cùng revision/outbox).
Mọi truy vấn đọc resource của legacy giới hạn `origin <> 'tool'` và chưa `removed`: legacy chỉ thấy inventory nhập từ web.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg

from integration_service.legacy_acl.mapper import MODE_TO_CANONICAL

LEGACY_RESOURCE = "NOT r.removed AND r.origin <> 'tool'"


# ── Platform policy ───────────────────────────────────────────────────────────────
# NGUỒN SỰ THẬT về "nền tảng nào tồn tại" là `platform_registry` (Admin/dash_admin tạo). `platform_policies` chỉ giữ HÀNH VI
# nhập web/collector của từng nền tảng; nền tảng chưa có dòng policy dùng mặc định: cho nhập tay VÀ giao cho Tool dùng chung
# (collector) kéo số liệu — collector tự chỉ nhận platform nó có adapter nên job platform chưa hỗ trợ chỉ nằm chờ. Nền tảng bị
# tắt trong registry thì không có policy (không nhập được).
_POLICY_SELECT = (
    "SELECT r.key AS platform, COALESCE(p.web_entry_enabled, true) AS web_entry_enabled, "
    "COALESCE(p.fallback_enabled, true) AS fallback_enabled, COALESCE(p.adapter_status, 'none') AS adapter_status, "
    "COALESCE(p.updated_by, '') AS updated_by, COALESCE(p.updated_at, r.updated_at) AS updated_at, "
    "r.display_name AS display_name FROM platform_registry r "
    "LEFT JOIN legacy_ingest.platform_policies p ON p.platform = r.key WHERE r.enabled"
)


async def list_policies(conn: asyncpg.Connection) -> list[dict]:
    return [dict(r) for r in await conn.fetch(_POLICY_SELECT + " ORDER BY r.key")]


async def get_policy(conn: asyncpg.Connection, platform: str) -> dict | None:
    row = await conn.fetchrow(_POLICY_SELECT + " AND r.key = $1", platform)
    return dict(row) if row else None


async def upsert_policy(conn: asyncpg.Connection, platform: str, web_entry: bool | None, fallback: bool | None,
                        adapter: str | None, updated_by: str, now: datetime) -> dict:
    """Cập nhật TỪNG PHẦN: field None giữ nguyên (hoặc mặc định nếu platform chưa có policy)."""
    row = await conn.fetchrow(
        "INSERT INTO legacy_ingest.platform_policies AS p (platform, web_entry_enabled, fallback_enabled, adapter_status, "
        "updated_by, updated_at) VALUES ($1, COALESCE($2, true), COALESCE($3, true), COALESCE($4, 'none'), $5, $6) "
        "ON CONFLICT (platform) DO UPDATE SET web_entry_enabled = COALESCE($2, p.web_entry_enabled), "
        "fallback_enabled = COALESCE($3, p.fallback_enabled), adapter_status = COALESCE($4, p.adapter_status), "
        "updated_by = $5, updated_at = $6 RETURNING *", platform, web_entry, fallback, adapter, updated_by, now)
    return dict(row)


# ── Source ────────────────────────────────────────────────────────────────────────

async def insert_source(conn: asyncpg.Connection, *, source_id: str, owner: str, name: str, platform: str,
                        project_key: str, default_country: str, now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO legacy_ingest.resource_sources (id, owner_account_id, name, platform, project_key, default_country, "
        "created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$7) RETURNING *",
        source_id, owner, name, platform, project_key, default_country, now)
    return dict(row)


async def list_sources(conn: asyncpg.Connection, owner: str | None) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM legacy_ingest.resource_sources WHERE ($1::text IS NULL OR owner_account_id = $1) "
        "ORDER BY platform, name, id", owner)
    return [dict(r) for r in rows]


async def get_source(conn: asyncpg.Connection, source_id: str, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM legacy_ingest.resource_sources WHERE id = $1" + (" FOR UPDATE" if lock else ""),
                              source_id)
    return dict(row) if row else None


async def update_source(conn: asyncpg.Connection, source_id: str, fields: dict, now: datetime) -> dict:
    row = await conn.fetchrow(
        "UPDATE legacy_ingest.resource_sources SET name = COALESCE($3, name), project_key = COALESCE($4, project_key), "
        "default_country = COALESCE($5, default_country), updated_at = $2 WHERE id = $1 RETURNING *",
        source_id, now, fields.get("name"), fields.get("project_key"), fields.get("default_country"))
    return dict(row)


# ── Resource (ĐỌC) + gán Source ─────────────────────────────────────────────────────

async def get_resource(conn: asyncpg.Connection, resource_id: str, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow(
        "SELECT r.* FROM resources r WHERE r.id = $1 AND " + LEGACY_RESOURCE + (" FOR UPDATE OF r" if lock else ""),
        resource_id)
    return dict(row) if row else None


async def find_by_key(conn: asyncpg.Connection, owner: str, platform: str, resource_type: str, external_id: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM resources WHERE owner_account_id = $1 AND platform = $2 AND resource_type = $3 AND external_id = $4",
        owner, platform, resource_type, external_id)
    return dict(row) if row else None


_LIST_RESOURCES = (
    "SELECT r.* FROM resources r WHERE " + LEGACY_RESOURCE + " "
    "AND ($1::text IS NULL OR r.owner_account_id = $1) "
    "AND ($2::text = '' OR r.platform = $2) "
    "AND ($3::text = '' OR COALESCE(r.data -> 'country' ->> 'name', r.data -> 'country' ->> 'code', '') = $3) "
    "AND ($4::text = '' OR r.origin = $4) "
    "AND ($5::text = '' OR COALESCE(r.data -> 'extensions' -> 'legacy' ->> 'mapping_status', 'unmapped') = $5) "
    "AND ($6::text = '' OR COALESCE(r.data -> 'extensions' -> 'legacy' ->> 'verification_status', 'unverified') = $6) "
    "AND ($7::text = '' OR r.execution_mode = $7) "
    "AND ($8::boolean IS NULL OR (r.state = 'active') = $8) "
    "AND ($9::text = '' OR r.id IN (SELECT resource_id FROM legacy_ingest.resource_assignments "
    "WHERE source_instance_id = $9)) "
    "AND ($10::text = '' OR COALESCE(r.data ->> 'display_name', '') ILIKE '%' || $10 || '%' "
    "OR COALESCE(r.data ->> 'username', '') ILIKE '%' || $10 || '%' "
    "OR COALESCE(r.data ->> 'url', '') ILIKE '%' || $10 || '%' "
    "OR COALESCE(r.data -> 'extensions' -> 'legacy' ->> 'input_ref', r.external_id) ILIKE '%' || $10 || '%') "
    "ORDER BY r.created_at DESC, r.revision DESC, r.id")


async def list_resources(conn: asyncpg.Connection, owner: str | None, filters: dict) -> list[dict]:
    """Bộ lọc của legacy (rỗng = không lọc), SQL tĩnh + tham số. `q` giữ ngữ nghĩa LIKE của legacy (`%`/`_` là ký tự đại diện)."""
    mode = filters.get("execution_mode") or ""
    rows = await conn.fetch(
        _LIST_RESOURCES, owner, filters.get("platform") or "", filters.get("country") or "", filters.get("origin") or "",
        filters.get("mapping_status") or "", filters.get("verification_status") or "",
        MODE_TO_CANONICAL.get(mode, mode), filters.get("enabled"), filters.get("source_instance_id") or "",
        (filters.get("q") or "").strip())
    return [dict(r) for r in rows]


async def source_ids_for(conn: asyncpg.Connection, resource_ids: list[str]) -> dict[str, list[str]]:
    if not resource_ids:
        return {}
    rows = await conn.fetch(
        "SELECT resource_id, source_instance_id FROM legacy_ingest.resource_assignments "
        "WHERE resource_id = ANY($1::text[]) ORDER BY created_at, id", resource_ids)
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["resource_id"], []).append(r["source_instance_id"])
    return out


async def add_assignment(conn: asyncpg.Connection, *, assignment_id: str, resource_id: str, source_id: str, owner: str,
                         now: datetime) -> bool:
    tag = await conn.execute(
        "INSERT INTO legacy_ingest.resource_assignments (id, resource_id, source_instance_id, owner_account_id, created_at, "
        "updated_at) VALUES ($1,$2,$3,$4,$5,$5) ON CONFLICT (resource_id, source_instance_id) DO NOTHING",
        assignment_id, resource_id, source_id, owner, now)
    return tag.endswith(" 1")


async def first_assignment_id(conn: asyncpg.Connection, resource_id: str) -> str:
    return await conn.fetchval("SELECT id FROM legacy_ingest.resource_assignments WHERE resource_id = $1 "
                               "ORDER BY created_at, id LIMIT 1", resource_id) or ""


async def delete_assignments(conn: asyncpg.Connection, resource_id: str) -> None:
    await conn.execute("DELETE FROM legacy_ingest.resource_assignments WHERE resource_id = $1", resource_id)


async def inventory_revision(conn: asyncpg.Connection, owner: str | None) -> tuple[datetime | None, int]:
    row = await conn.fetchrow(
        "SELECT max(r.updated_at) AS m, count(*) AS n FROM resources r WHERE " + LEGACY_RESOURCE + " "
        "AND ($1::text IS NULL OR r.owner_account_id = $1)", owner)
    return row["m"], row["n"]


async def list_inventory_owners(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(
        "SELECT DISTINCT r.owner_account_id AS id, COALESCE(p.username, '') AS username FROM resources r "
        "LEFT JOIN account_projection p ON p.account_id = r.owner_account_id WHERE " + LEGACY_RESOURCE + " "
        "ORDER BY username, id")
    return [dict(r) for r in rows]


async def usernames(conn: asyncpg.Connection, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = await conn.fetch("SELECT account_id, username FROM account_projection WHERE account_id = ANY($1::text[])", ids)
    return {r["account_id"]: r["username"] for r in rows}


# ── Run Queue ─────────────────────────────────────────────────────────────────────

async def insert_batch(conn: asyncpg.Connection, *, batch_id: str, created_by: str, platform: str, name: str,
                       now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO legacy_ingest.operation_batches (id, created_by, platform, name, status, created_at, updated_at) "
        "VALUES ($1,$2,$3,$4,'open',$5,$5) RETURNING *", batch_id, created_by, platform, name, now)
    return dict(row)


async def list_batches(conn: asyncpg.Connection, created_by: str | None) -> list[dict]:
    rows = await conn.fetch(
        "SELECT * FROM legacy_ingest.operation_batches WHERE ($1::text IS NULL OR created_by = $1) "
        "ORDER BY created_at DESC, seq DESC", created_by)
    return [dict(r) for r in rows]


async def get_batch(conn: asyncpg.Connection, batch_id: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM legacy_ingest.operation_batches WHERE id = $1", batch_id)
    return dict(row) if row else None


_ITEM_SELECT = (
    "SELECT i.*, j.status AS job_status, j.attempts AS job_attempts, "
    "COALESCE(NULLIF(j.last_error_message, ''), j.last_error_code, '') AS job_last_error, j.updated_at AS job_updated_at "
    "FROM legacy_ingest.operation_items i LEFT JOIN jobs j ON j.id = i.job_id ")


async def insert_item(conn: asyncpg.Connection, *, item_id: str, batch_id: str, resource_id: str, assignment_id: str,
                      owner: str, platform: str, status: str, job_id: str | None, now: datetime) -> None:
    await conn.execute(
        "INSERT INTO legacy_ingest.operation_items (id, batch_id, resource_id, source_assignment_id, owner_account_id, "
        "platform, status, job_id, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9)",
        item_id, batch_id, resource_id, assignment_id, owner, platform, status, job_id, now)


async def list_items(conn: asyncpg.Connection, batch_id: str) -> list[dict]:
    rows = await conn.fetch(_ITEM_SELECT + "WHERE i.batch_id = $1 ORDER BY i.created_at, i.seq", batch_id)
    return [dict(r) for r in rows]


async def get_item(conn: asyncpg.Connection, item_id: str, *, lock: bool = False) -> dict | None:
    if lock:   # khoá item (và job gắn kèm) để huỷ không chạy đua với worker complete/fail
        await conn.fetchrow("SELECT i.id FROM legacy_ingest.operation_items i WHERE i.id = $1 FOR UPDATE", item_id)
    row = await conn.fetchrow(_ITEM_SELECT + "WHERE i.id = $1", item_id)
    return dict(row) if row else None


async def set_item_status(conn: asyncpg.Connection, item_id: str, status: str, now: datetime) -> None:
    await conn.execute("UPDATE legacy_ingest.operation_items SET status = $2, updated_at = $3 WHERE id = $1",
                       item_id, status, now)


async def insert_audit(conn: asyncpg.Connection, *, account_id: str, event_type: str, target_type: str, target_id: str,
                       payload: dict, now: datetime) -> None:
    await conn.execute(
        "INSERT INTO legacy_ingest.operation_audit_log (account_id, event_type, target_type, target_id, payload, at) "
        "VALUES ($1,$2,$3,$4,$5,$6)", account_id, event_type, target_type, target_id, payload, now)


# ── Pairing (account_links / link_codes) ──────────────────────────────────────────

async def insert_link_code(conn: asyncpg.Connection, code: str, account_id: str, now: datetime, expires: datetime) -> None:
    await conn.execute("INSERT INTO legacy_ingest.link_codes (code, account_id, created_at, expires_at) VALUES ($1,$2,$3,$4)",
                       code, account_id, now, expires)


async def redeem_code(conn: asyncpg.Connection, code: str, tool_user_id: str, now: datetime) -> str | None:
    """Đổi mã (nguyên tử): khoá dòng mã, kiểm hạn, đánh `used`, ghi liên kết. Trả account_id hoặc None."""
    row = await conn.fetchrow("SELECT * FROM legacy_ingest.link_codes WHERE code = $1 AND status = 'pending' FOR UPDATE", code)
    if row is None:
        return None
    if row["expires_at"] < now:
        await conn.execute("UPDATE legacy_ingest.link_codes SET status = 'expired' WHERE code = $1", code)
        return None
    await conn.execute("UPDATE legacy_ingest.link_codes SET status = 'used', used_at = $2 WHERE code = $1", code, now)
    await conn.execute(
        "INSERT INTO legacy_ingest.account_links (account_id, tool_user_id, linked_at) VALUES ($1,$2,$3) "
        "ON CONFLICT (account_id, tool_user_id) DO UPDATE SET status = 'active'", row["account_id"], tool_user_id, now)
    return row["account_id"]


async def list_links(conn: asyncpg.Connection, account_id: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT tool_user_id, label, status, linked_at FROM legacy_ingest.account_links "
        "WHERE account_id = $1 AND status = 'active' ORDER BY linked_at DESC, tool_user_id", account_id)
    return [dict(r) for r in rows]


async def revoke_link(conn: asyncpg.Connection, account_id: str, tool_user_id: str) -> bool:
    tag = await conn.execute(
        "UPDATE legacy_ingest.account_links SET status = 'revoked' "
        "WHERE account_id = $1 AND tool_user_id = $2 AND status = 'active'", account_id, tool_user_id)
    return tag.endswith(" 1")
