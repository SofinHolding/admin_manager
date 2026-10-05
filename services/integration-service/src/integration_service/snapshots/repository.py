"""SQL của snapshots + snapshot_items (staging)."""

from __future__ import annotations

from datetime import datetime

import asyncpg


async def insert(conn: asyncpg.Connection, *, id: str, owner: str, client_id: str, installation_id: str | None,
                 platform: str, snapshot_type: str, observed_at: datetime, report_timezone: str, scope: dict | None,
                 expected_counts: dict, now: datetime) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO snapshots (id, owner_account_id, client_id, installation_id, platform, snapshot_type, status, "
        "observed_at, report_timezone, scope, expected_counts, created_at, updated_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,'building',$7,$8,$9,$10,$11,$11) RETURNING *",
        id, owner, client_id, installation_id, platform, snapshot_type, observed_at, report_timezone, scope,
        expected_counts, now)
    return dict(row)


async def get_for_client(conn: asyncpg.Connection, snapshot_id: str, owner: str, client_id: str, *,
                         lock: bool = False) -> dict | None:
    """Snapshot chỉ hiện với CHÍNH client tạo ra nó (client khác của cùng owner cũng không thấy)."""
    row = await conn.fetchrow(
        "SELECT * FROM snapshots WHERE id = $1 AND owner_account_id = $2 AND client_id = $3"
        + (" FOR UPDATE" if lock else ""), snapshot_id, owner, client_id)
    return dict(row) if row else None


async def last_timezone(conn: asyncpg.Connection, owner: str, client_id: str, platform: str) -> str | None:
    return await conn.fetchval(
        "SELECT report_timezone FROM snapshots WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 "
        "AND status = 'committed' ORDER BY committed_at DESC, id LIMIT 1", owner, client_id, platform)


async def set_status(conn: asyncpg.Connection, snapshot_id: str, status: str, now: datetime, *,
                     expect: str | None = None, reason: str | None = None) -> bool:
    got = await conn.fetchval(
        "UPDATE snapshots SET status = $2, updated_at = $3, failure_reason = COALESCE($5, failure_reason) "
        "WHERE id = $1 AND ($4::text IS NULL OR status = $4) RETURNING id", snapshot_id, status, now, expect, reason)
    return got is not None


async def touch(conn: asyncpg.Connection, snapshot_id: str, now: datetime) -> None:
    await conn.execute("UPDATE snapshots SET updated_at = $2 WHERE id = $1", snapshot_id, now)


async def mark_committed(conn: asyncpg.Connection, snapshot_id: str, *, now: datetime, result: dict, revision_from: int,
                         revision_to: int, accept_partial: bool) -> dict:
    row = await conn.fetchrow(
        "UPDATE snapshots SET status = 'committed', committed_at = $2, updated_at = $2, result = $3, "
        "revision_from = $4, revision_to = $5, accept_partial = $6 WHERE id = $1 RETURNING *",
        snapshot_id, now, result, revision_from, revision_to, accept_partial)
    return dict(row)


async def flip_current(conn: asyncpg.Connection, snap: dict) -> bool:
    """Con trỏ `current` theo (owner, client, platform, type) chỉ đổi khi commit thành công và KHÔNG lùi về snapshot
    cũ hơn (observed_at) snapshot đang là current."""
    newer = await conn.fetchval(
        "SELECT 1 FROM snapshots WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 AND snapshot_type = $4 "
        "AND is_current AND observed_at > $5 AND id <> $6", snap["owner_account_id"], snap["client_id"],
        snap["platform"], snap["snapshot_type"], snap["observed_at"], snap["id"])
    if newer:
        return False
    await conn.execute(
        "UPDATE snapshots SET is_current = false WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 "
        "AND snapshot_type = $4 AND is_current AND id <> $5", snap["owner_account_id"], snap["client_id"],
        snap["platform"], snap["snapshot_type"], snap["id"])
    await conn.execute("UPDATE snapshots SET is_current = true WHERE id = $1", snap["id"])
    return True


async def newer_full_scopes(conn: asyncpg.Connection, snap: dict) -> list[dict]:
    """Scope của các snapshot `full` ĐÃ commit, cùng (owner, client, platform), có observed_at mới hơn snapshot này."""
    rows = await conn.fetch(
        "SELECT scope FROM snapshots WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 "
        "AND snapshot_type = 'full' AND status = 'committed' AND observed_at > $4 AND id <> $5",
        snap["owner_account_id"], snap["client_id"], snap["platform"], snap["observed_at"], snap["id"])
    return [r["scope"] or {} for r in rows]


# ── Staging ──────────────────────────────────────────────────────────────────────

async def staged_for_kind(conn: asyncpg.Connection, snapshot_id: str, kind: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT item_key, observed_at, payload, idx, external_id FROM snapshot_items "
        "WHERE snapshot_id = $1 AND kind = $2 AND status = 'staged' ORDER BY id", snapshot_id, kind)


async def staged_map(conn: asyncpg.Connection, snapshot_id: str, kind: str, keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    rows = await conn.fetch(
        "SELECT item_key, observed_at, payload FROM snapshot_items WHERE snapshot_id = $1 AND kind = $2 "
        "AND status = 'staged' AND item_key = ANY($3::text[])", snapshot_id, kind, keys)
    return {r["item_key"]: {"observed_at": r["observed_at"], "payload": r["payload"]} for r in rows}


async def staged_external_ids(conn: asyncpg.Connection, snapshot_id: str, kind: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = await conn.fetch(
        "SELECT external_id FROM snapshot_items WHERE snapshot_id = $1 AND kind = $2 AND status = 'staged' "
        "AND external_id = ANY($3::text[])", snapshot_id, kind, ids)
    return {r["external_id"] for r in rows}


async def stage_many(conn: asyncpg.Connection, snapshot_id: str, kind: str,
                     rows: list[tuple[str, datetime, dict, int, str]]) -> None:
    """Upsert vào staging: trong cùng snapshot, bản có observed_at mới hơn (hoặc bằng) thắng."""
    if not rows:
        return
    await conn.execute(
        "INSERT INTO snapshot_items (snapshot_id, kind, item_key, status, observed_at, payload, idx, external_id) "
        "SELECT $1, $2, t.k, 'staged', t.o, t.p, t.i, t.e FROM unnest($3::text[], $4::timestamptz[], $5::jsonb[], "
        "$6::int[], $7::text[]) AS t(k, o, p, i, e) "
        "ON CONFLICT (snapshot_id, kind, item_key) WHERE status = 'staged' DO UPDATE SET payload = EXCLUDED.payload, "
        "observed_at = EXCLUDED.observed_at, idx = EXCLUDED.idx, external_id = EXCLUDED.external_id "
        "WHERE EXCLUDED.observed_at >= snapshot_items.observed_at",
        snapshot_id, kind, [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
        [r[4] for r in rows])


async def clear_rejections(conn: asyncpg.Connection, snapshot_id: str, kind: str, item_keys: list[str]) -> None:
    """Bản ghi đã gửi lại ĐÚNG (cùng khoá tự nhiên) thì không còn tính là bị từ chối."""
    if item_keys:
        await conn.execute("DELETE FROM snapshot_items WHERE snapshot_id = $1 AND kind = $2 AND status = 'rejected' "
                           "AND item_key = ANY($3::text[])", snapshot_id, kind, item_keys)


async def add_rejections(conn: asyncpg.Connection, snapshot_id: str, kind: str,
                         rows: list[tuple[int, str | None, str, str, str | None]], cap: int) -> None:
    """rows: (index, external_id, code, message, item_key)."""
    if not rows:
        return
    have = await conn.fetchval("SELECT count(*) FROM snapshot_items WHERE snapshot_id = $1 AND status = 'rejected'",
                               snapshot_id)
    rows = rows[: max(0, cap - have)]
    await clear_rejections(conn, snapshot_id, kind, [r[4] for r in rows if r[4]])
    await conn.executemany(
        "INSERT INTO snapshot_items (snapshot_id, kind, item_key, status, idx, external_id, code, message) "
        "VALUES ($1, $2, $3, 'rejected', $4, $5, $6, $7)",
        [(snapshot_id, kind, r[4] or f"rej:{r[0]}", r[0], r[1], r[2], r[3]) for r in rows])


async def counts(conn: asyncpg.Connection, snapshot_id: str) -> tuple[dict[str, int], dict[str, int]]:
    """(staged theo kind, rejected theo kind)."""
    rows = await conn.fetch("SELECT kind, status, count(*) AS n FROM snapshot_items WHERE snapshot_id = $1 "
                            "GROUP BY kind, status", snapshot_id)
    staged = {k: 0 for k in ("resources", "content", "metrics", "events")}
    rejected = dict(staged)
    for r in rows:
        (staged if r["status"] == "staged" else rejected)[r["kind"]] = r["n"]
    return staged, rejected


async def rejection_rows(conn: asyncpg.Connection, snapshot_id: str, limit: int) -> list[dict]:
    rows = await conn.fetch(
        "SELECT kind, idx, external_id, code, message FROM snapshot_items WHERE snapshot_id = $1 AND status = 'rejected' "
        "ORDER BY id LIMIT $2", snapshot_id, limit)
    return [dict(r) for r in rows]


async def delete_staged(conn: asyncpg.Connection, snapshot_id: str) -> None:
    await conn.execute("DELETE FROM snapshot_items WHERE snapshot_id = $1 AND status = 'staged'", snapshot_id)


# ── Bảo trì ──────────────────────────────────────────────────────────────────────

async def abandon_stale(conn: asyncpg.Connection, cutoff: datetime, now: datetime) -> list[str]:
    rows = await conn.fetch(
        "UPDATE snapshots SET status = 'abandoned', updated_at = $2, failure_reason = 'ttl_expired' "
        "WHERE status = 'building' AND updated_at < $1 RETURNING id", cutoff, now)
    return [r["id"] for r in rows]


async def fail_stuck_committing(conn: asyncpg.Connection, cutoff: datetime, now: datetime) -> list[str]:
    rows = await conn.fetch(
        "UPDATE snapshots SET status = 'failed', updated_at = $2, failure_reason = 'commit_interrupted' "
        "WHERE status = 'committing' AND updated_at < $1 RETURNING id", cutoff, now)
    return [r["id"] for r in rows]


async def purge_dead_items(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM snapshot_items WHERE status = 'staged' AND snapshot_id IN "
                       "(SELECT id FROM snapshots WHERE status IN ('abandoned', 'failed'))")
