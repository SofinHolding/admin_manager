"""SQL của read model + sổ sách. Repository nhận `conn` (đã nằm trong transaction nếu cần), chỉ chạm bảng của service.

Upsert theo khoá tự nhiên và CHỈ ghi đè khi revision đến >= revision đang lưu: áp lại cùng một revision (replay) là
idempotent, bản canonical cũ đến muộn không làm số liệu lùi.
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from analytics_service.readmodels.canonical import checksum_line, checksum_lines, natural_key

_UPSERT = {
    "projects": (
        "INSERT INTO read_projects (owner_account_id, project_key, name, platform, country, enabled, observed_at,"
        " revision, removed) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)"
        " ON CONFLICT (owner_account_id, project_key) DO UPDATE SET name=EXCLUDED.name, platform=EXCLUDED.platform,"
        " country=EXCLUDED.country, enabled=EXCLUDED.enabled, observed_at=EXCLUDED.observed_at,"
        " revision=EXCLUDED.revision, removed=EXCLUDED.removed WHERE read_projects.revision <= EXCLUDED.revision",
        ("owner_account_id", "project_key", "name", "platform", "country", "enabled", "observed_at", "revision",
         "removed")),
    "resources": (
        "INSERT INTO read_resources (owner_account_id, platform, external_id, resource_type, client_id, display_name,"
        " username, url, country, state, projects, managed, active, observed_at, revision, removed)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16)"
        " ON CONFLICT (owner_account_id, platform, external_id) DO UPDATE SET resource_type=EXCLUDED.resource_type,"
        " client_id=EXCLUDED.client_id, display_name=EXCLUDED.display_name, username=EXCLUDED.username,"
        " url=EXCLUDED.url, country=EXCLUDED.country, state=EXCLUDED.state, projects=EXCLUDED.projects,"
        " managed=EXCLUDED.managed, active=EXCLUDED.active, observed_at=EXCLUDED.observed_at,"
        " revision=EXCLUDED.revision, removed=EXCLUDED.removed WHERE read_resources.revision <= EXCLUDED.revision",
        ("owner_account_id", "platform", "external_id", "resource_type", "client_id", "display_name", "username",
         "url", "country", "state", "projects", "managed", "active", "observed_at", "revision", "removed")),
    "content": (
        "INSERT INTO read_content (owner_account_id, platform, resource_external_id, external_id, published_date,"
        " views, reactions, comments, state, observed_at, revision, removed)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)"
        " ON CONFLICT (owner_account_id, platform, resource_external_id, external_id) DO UPDATE SET"
        " published_date=EXCLUDED.published_date, views=EXCLUDED.views, reactions=EXCLUDED.reactions,"
        " comments=EXCLUDED.comments, state=EXCLUDED.state, observed_at=EXCLUDED.observed_at,"
        " revision=EXCLUDED.revision, removed=EXCLUDED.removed WHERE read_content.revision <= EXCLUDED.revision",
        ("owner_account_id", "platform", "resource_external_id", "external_id", "published_date", "views",
         "reactions", "comments", "state", "observed_at", "revision", "removed")),
    "metrics": (
        "INSERT INTO read_daily_metrics (owner_account_id, platform, resource_external_id, metric_date, views,"
        " metrics, observed_at, revision, removed) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9)"
        " ON CONFLICT (owner_account_id, platform, resource_external_id, metric_date) DO UPDATE SET"
        " views=EXCLUDED.views, metrics=EXCLUDED.metrics, observed_at=EXCLUDED.observed_at,"
        " revision=EXCLUDED.revision, removed=EXCLUDED.removed WHERE read_daily_metrics.revision <= EXCLUDED.revision",
        ("owner_account_id", "platform", "resource_external_id", "metric_date", "views", "metrics", "observed_at",
         "revision", "removed")),
    "events": (
        "INSERT INTO read_events (owner_account_id, external_id, platform, resource_external_id, kind,"
        " occurred_date, project_key, observed_at, revision, removed) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)"
        " ON CONFLICT (owner_account_id, external_id) DO UPDATE SET platform=EXCLUDED.platform,"
        " resource_external_id=EXCLUDED.resource_external_id, kind=EXCLUDED.kind,"
        " occurred_date=EXCLUDED.occurred_date, project_key=EXCLUDED.project_key,"
        " observed_at=EXCLUDED.observed_at, revision=EXCLUDED.revision, removed=EXCLUDED.removed"
        " WHERE read_events.revision <= EXCLUDED.revision",
        ("owner_account_id", "external_id", "platform", "resource_external_id", "kind", "occurred_date",
         "project_key", "observed_at", "revision", "removed")),
}

_TABLE = {"projects": "read_projects", "resources": "read_resources", "content": "read_content",
          "metrics": "read_daily_metrics", "events": "read_events"}

_KEY_COLUMNS = {
    "projects": "owner_account_id, project_key",
    "resources": "owner_account_id, platform, external_id",
    "content": "owner_account_id, platform, resource_external_id, external_id, views, reactions, comments",
    "metrics": "owner_account_id, platform, resource_external_id, metric_date, views",
    "events": "owner_account_id, external_id",
}


async def upsert_rows(conn: asyncpg.Connection, kind: str, rows: list[dict]) -> None:
    if not rows:
        return
    sql, columns = _UPSERT[kind]
    args = []
    for row in rows:
        values = []
        for column in columns:
            value = row[column]
            values.append(value)
        args.append(values)
    await conn.executemany(sql, args)


async def clear_read_model(conn: asyncpg.Connection) -> None:
    """Rebuild: xoá sạch read model (trong CÙNG transaction với việc nạp lại nên người đọc không thấy trạng thái rỗng)."""
    await conn.execute("TRUNCATE read_projects, read_resources, read_content, read_daily_metrics, read_events")


async def head_revision(conn: asyncpg.Connection) -> int:
    best = 0
    for table in _TABLE.values():
        best = max(best, await conn.fetchval(f"SELECT COALESCE(MAX(revision), 0) FROM {table}"))  # noqa: S608
    return best


async def local_summary(conn: asyncpg.Connection) -> dict:
    """Đếm + checksum của read model, cùng định dạng với `canonical/summary` của Integration."""
    counts: dict[str, dict] = {}
    checksum: dict[str, str] = {}
    for kind, table in _TABLE.items():
        rows = await conn.fetch(f"SELECT {_KEY_COLUMNS[kind]}, revision, removed FROM {table}")  # noqa: S608
        lines = []
        active = 0
        for r in rows:
            row = dict(r)
            lines.append(checksum_line(kind, row))
            active += 0 if row["removed"] else 1
        counts[kind] = {"total": len(rows), "active": active}
        checksum[kind] = checksum_lines(lines)
    return {"head_revision": await head_revision(conn), "counts": counts, "checksum": checksum}


# ── Sổ sách: con trỏ, event đã xử lý, dead letter, trạng thái ─────────────────

async def get_cursor(conn: asyncpg.Connection, source: str) -> int:
    value = await conn.fetchval("SELECT seq FROM feed_cursor WHERE source = $1", source)
    return int(value or 0)


async def set_cursor(conn: asyncpg.Connection, source: str, seq: int) -> None:
    await conn.execute(
        "INSERT INTO feed_cursor (source, seq, updated_at) VALUES ($1, $2, now())"
        " ON CONFLICT (source) DO UPDATE SET seq = EXCLUDED.seq, updated_at = now()", source, seq)


async def all_cursors(conn: asyncpg.Connection) -> dict[str, int]:
    return {r["source"]: int(r["seq"]) for r in await conn.fetch("SELECT source, seq FROM feed_cursor")}


async def processed_ids(conn: asyncpg.Connection, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    return {r["id"] for r in await conn.fetch("SELECT id FROM processed_events WHERE id = ANY($1::text[])", ids)}


async def mark_processed(conn: asyncpg.Connection, source: str, events: list[dict]) -> int:
    """Ghi event đã xử lý; trả về SỐ event mới (trùng id = no-op)."""
    if not events:
        return 0
    rows = await conn.fetch(
        "INSERT INTO processed_events (id, source, seq, type) SELECT * FROM unnest($1::text[], $2::text[],"
        " $3::bigint[], $4::text[]) ON CONFLICT (id) DO NOTHING RETURNING id",
        [e["id"] for e in events], [source] * len(events), [int(e["seq"]) for e in events],
        [e["type"] for e in events])
    return len(rows)


async def put_dead_letter(conn: asyncpg.Connection, *, dl_id: str, source: str, seq: int | None, type_: str,
                          error: str, attempts: int, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO dead_letters (id, source, seq, type, error, attempts, payload) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)"
        " ON CONFLICT (id) DO UPDATE SET error = EXCLUDED.error, attempts = dead_letters.attempts + EXCLUDED.attempts,"
        " last_attempt_at = now(), replayed_at = NULL",
        dl_id, source, seq, type_, error[:500], attempts, payload)


async def open_dead_letters(conn: asyncpg.Connection, limit: int = 100) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, source, seq, type, error, attempts, payload, created_at, last_attempt_at FROM dead_letters"
        " WHERE replayed_at IS NULL ORDER BY created_at, id LIMIT $1", limit)
    return [dict(r) for r in rows]


async def count_dead_letters(conn: asyncpg.Connection) -> dict:
    row = await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE replayed_at IS NULL) AS open, count(*) AS total FROM dead_letters")
    return {"open": int(row["open"]), "total": int(row["total"])}


async def resolve_dead_letter(conn: asyncpg.Connection, dl_id: str) -> None:
    await conn.execute("UPDATE dead_letters SET replayed_at = now(), last_attempt_at = now() WHERE id = $1", dl_id)


async def fail_dead_letter(conn: asyncpg.Connection, dl_id: str, error: str) -> None:
    await conn.execute(
        "UPDATE dead_letters SET attempts = attempts + 1, error = $2, last_attempt_at = now() WHERE id = $1",
        dl_id, error[:500])


async def get_state(conn: asyncpg.Connection, key: str) -> dict:
    row = await conn.fetchrow("SELECT num, value, updated_at FROM sync_state WHERE key = $1", key)
    if row is None:
        return {}
    value = row["value"] if isinstance(row["value"], dict) else {}
    return {**value, "_num": int(row["num"]), "_updated_at": row["updated_at"]}


async def put_state(conn: asyncpg.Connection, key: str, value: dict) -> None:
    await conn.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES ($1, $2::jsonb, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", key, value)


async def bump_seal(conn: asyncpg.Connection) -> int:
    """Tăng seal phiên bản trong khoá cache. GỌI TRONG CÙNG transaction với thay đổi dữ liệu: seal mới chỉ nhìn thấy
    được sau khi dữ liệu đã commit, nên một request thấy seal mới thì chắc chắn đọc dữ liệu mới."""
    return int(await conn.fetchval(
        "UPDATE sync_state SET num = num + 1, updated_at = now() WHERE key = 'seal' RETURNING num"))


async def read_seal(conn: asyncpg.Connection) -> int:
    return int(await conn.fetchval("SELECT num FROM sync_state WHERE key = 'seal'") or 0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["natural_key"]
