"""Hợp nhất bản ghi đã chuẩn hoá vào kho canonical (staging -> canonical) với quy tắc của hợp đồng ingest:

  R1 upsert theo khoá tự nhiên · R2 `observed_at` mới hơn thắng (cũ hơn => stale_ignored) · R3 số tuyệt đối, ghi đè ·
  R5 suy luận `removed` cho snapshot `full` theo `scope` · `null` ≠ `0` (lưu nguyên JSON).

MỌI hàm ở đây chạy TRONG transaction của người gọi và yêu cầu người gọi đã giữ khoá thứ tự commit
(`feed.repository.acquire_order_lock`) TRƯỚC khi đọc trạng thái canonical, để đọc-rồi-ghi không bị chen ngang và
`revision` tăng đúng thứ tự commit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import asyncpg

from integration_service.feed import events
from integration_service.feed import repository as outbox
from integration_service.ingest.validate import Staged
from integration_service.security import new_id


@dataclass
class MergeStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    stale_ignored: int = 0
    present_ids: list[str] = field(default_factory=list)   # id canonical của MỌI khoá có trong tập gửi (kể cả stale)
    revisions: list[int] = field(default_factory=list)
    changed: list[tuple[str, str]] = field(default_factory=list)  # (id, 'created'|'updated') — cho resource.changed
    outcomes: list[tuple[int, str, str]] = field(default_factory=list)  # (index, id, verdict) theo từng bản ghi gửi lên

    def as_dict(self) -> dict:
        return {"created": self.created, "updated": self.updated, "unchanged": self.unchanged,
                "stale_ignored": self.stale_ignored}


def decide(row: dict | None, observed_at: datetime, data: dict, *, immutable: bool = False) -> str:
    """created | updated | unchanged | stale. Hàm thuần dùng cho cả staging (báo trước) lẫn commit (quyết định thật)."""
    if row is None:
        return "created"
    if immutable:  # sự kiện bất biến: bản đầu tiên thắng
        if row["data"] == data:
            return "updated" if row["removed"] else "unchanged"
        return "stale"
    if observed_at < row["observed_at"]:
        return "stale"
    if row["removed"]:
        return "updated"  # xuất hiện lại sau khi bị đánh dấu removed
    return "unchanged" if row["data"] == data else "updated"


# ── Resource ─────────────────────────────────────────────────────────────────────

_RESOURCE_KEYS = ("resource_type", "external_id", "observed_at", "platform")


def resource_data(payload: dict, existing: dict | None) -> dict:
    data = {k: v for k, v in payload.items() if k not in _RESOURCE_KEYS}
    if data.get("projects") is None:  # không khai `projects` => giữ nguyên liên kết cũ
        data["projects"] = (existing or {}).get("projects", [])
    return data


async def fetch_resources(conn: asyncpg.Connection, owner: str, platform: str,
                          keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    if not keys:
        return {}
    rows = await conn.fetch(
        "SELECT * FROM resources WHERE owner_account_id = $1 AND platform = $2 "
        "AND (resource_type, external_id) IN (SELECT * FROM unnest($3::text[], $4::text[]))",
        owner, platform, [k[0] for k in keys], [k[1] for k in keys])
    return {(r["resource_type"], r["external_id"]): dict(r) for r in rows}


async def resolve_resource_ids(conn: asyncpg.Connection, owner: str, platform: str,
                               external_ids: list[str]) -> dict[str, str]:
    """external_id -> resource id (ưu tiên bản chưa removed, rồi bản mới cập nhật gần nhất)."""
    if not external_ids:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (external_id) external_id, id FROM resources WHERE owner_account_id = $1 AND platform = $2 "
        "AND external_id = ANY($3::text[]) ORDER BY external_id, removed, updated_at DESC, id", owner, platform,
        external_ids)
    return {r["external_id"]: r["id"] for r in rows}


async def _ensure_projects(conn: asyncpg.Connection, owner: str, client_id: str, platform: str, keys: set[str],
                           now: datetime, stats: MergeStats) -> dict[str, str]:
    """Project được nhắc tới trong resource tự được tạo (stub) — không có bước Admin mapping."""
    if not keys:
        return {}
    rows = await conn.fetch("SELECT project_key, id FROM projects WHERE owner_account_id = $1 AND project_key = ANY($2::text[])",
                            owner, sorted(keys))
    ids = {r["project_key"]: r["id"] for r in rows}
    missing = sorted(keys - set(ids))
    if missing:
        revs = await outbox.next_revisions(conn, len(missing))
        stats.revisions += revs
        for key, rev in zip(missing, revs, strict=True):
            pid = new_id("prj")
            await conn.execute(
                "INSERT INTO projects (id, owner_account_id, client_id, project_key, platform, name, revision, created_at, "
                "updated_at) VALUES ($1,$2,$3,$4,$5,$4,$6,$7,$7)", pid, owner, client_id, key, platform, rev, now)
            ids[key] = pid
    return ids


async def merge_resources(conn: asyncpg.Connection, *, owner: str, client_id: str, platform: str,
                          items: list[Staged], snapshot_id: str | None, now: datetime, origin: str | None = None,
                          execution_mode: str | None = None) -> MergeStats:
    """`origin`/`execution_mode` (tuỳ chọn) đặt cột tương ứng — dùng bởi Legacy ACL cho resource nhập từ web; Tool không
    truyền (None => giữ giá trị mặc định/hiện có)."""
    stats = MergeStats()
    keys = [(s.payload["resource_type"], s.payload["external_id"]) for s in items]
    existing = await fetch_resources(conn, owner, platform, keys)
    plan: list[tuple[str, Staged, dict | None, dict]] = []
    for s, key in zip(items, keys, strict=True):
        row = existing.get(key)
        data = resource_data(s.payload, row["data"] if row else None)
        verdict = decide(row, s.observed_at, data)
        if verdict == "stale":
            stats.stale_ignored += 1
            stats.present_ids.append(row["id"])  # type: ignore[index]
            stats.outcomes.append((s.index, row["id"], "stale_ignored"))  # type: ignore[index]
        elif verdict == "unchanged":
            stats.unchanged += 1
            stats.present_ids.append(row["id"])  # type: ignore[index]
            stats.outcomes.append((s.index, row["id"], "unchanged"))  # type: ignore[index]
            if s.observed_at != row["observed_at"]:  # type: ignore[index]
                await conn.execute("UPDATE resources SET observed_at = $2, last_snapshot_id = COALESCE($3, last_snapshot_id) "
                                   "WHERE id = $1", row["id"], s.observed_at, snapshot_id)  # type: ignore[index]
        else:
            plan.append((verdict, s, row, data))
    if not plan:
        return stats

    revs = await outbox.next_revisions(conn, len(plan))
    stats.revisions += revs
    project_keys = {p["project_key"] for _, _, _, d in plan for p in (d.get("projects") or [])}
    project_ids = await _ensure_projects(conn, owner, client_id, platform, project_keys, now, stats)
    for (verdict, s, row, data), rev in zip(plan, revs, strict=True):
        if verdict == "created":
            rid = new_id("res")
            await conn.execute(
                "INSERT INTO resources (id, owner_account_id, client_id, platform, resource_type, external_id, state, "
                "observed_at, data, removed, revision, last_snapshot_id, created_at, updated_at, origin, execution_mode) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,false,$10,$11,$12,$12,COALESCE($13,'tool'),COALESCE($14,'primary_tool'))",
                rid, owner, client_id, platform, s.payload["resource_type"], s.payload["external_id"], data["state"],
                s.observed_at, data, rev, snapshot_id, now, origin, execution_mode)
            stats.created += 1
        else:
            rid = row["id"]  # type: ignore[index]
            await conn.execute(
                "UPDATE resources SET client_id = $2, state = $3, observed_at = $4, data = $5, removed = false, "
                "revision = $6, last_snapshot_id = COALESCE($7, last_snapshot_id), updated_at = $8, "
                "origin = COALESCE($9, origin), execution_mode = COALESCE($10, execution_mode) WHERE id = $1",
                rid, client_id, data["state"], s.observed_at, data, rev, snapshot_id, now, origin, execution_mode)
            stats.updated += 1
        stats.present_ids.append(rid)
        stats.changed.append((rid, verdict))
        stats.outcomes.append((s.index, rid, verdict))
        await conn.execute("DELETE FROM resource_project_bindings WHERE resource_id = $1", rid)
        for ref in data.get("projects") or []:
            await conn.execute(
                "INSERT INTO resource_project_bindings (resource_id, project_id, enabled, revision) VALUES ($1,$2,$3,$4)",
                rid, project_ids.get(ref["project_key"]) or await _project_id(conn, owner, ref["project_key"]),
                ref["enabled"], rev)
    return stats


async def remove_resource(conn: asyncpg.Connection, resource_id: str, now: datetime) -> bool:
    """Gỡ resource khỏi kho canonical (đánh `removed`, giữ lịch sử) — cùng cơ chế revision + `resource.changed` như
    admin bật/tắt. False nếu không có hoặc đã gỡ."""
    await outbox.acquire_order_lock(conn)
    row = await conn.fetchrow("SELECT * FROM resources WHERE id = $1 FOR UPDATE", resource_id)
    if row is None or row["removed"]:
        return False
    (rev,) = await outbox.next_revisions(conn, 1)
    await conn.execute("UPDATE resources SET removed = true, revision = $2, updated_at = $3 WHERE id = $1",
                       resource_id, rev, now)
    await events.resource_changed(conn, resource_id=resource_id, owner_account_id=row["owner_account_id"],
                                  platform=row["platform"], external_id=row["external_id"], state=row["state"],
                                  revision=rev)
    return True


async def _project_id(conn: asyncpg.Connection, owner: str, key: str) -> str:
    return await conn.fetchval("SELECT id FROM projects WHERE owner_account_id = $1 AND project_key = $2", owner, key)


async def emit_resource_changes(conn: asyncpg.Connection, owner: str, platform: str, stats: MergeStats) -> None:
    """`resource.changed`: thay đổi resource NGOÀI snapshot (batch trực tiếp, admin bật/tắt)."""
    if not stats.changed:
        return
    rows = await conn.fetch("SELECT id, external_id, state, revision FROM resources WHERE id = ANY($1::text[])",
                            [rid for rid, _ in stats.changed])
    for r in sorted(rows, key=lambda r: r["revision"]):
        await events.resource_changed(conn, resource_id=r["id"], owner_account_id=owner, platform=platform,
                                      external_id=r["external_id"], state=r["state"], revision=r["revision"])


# ── Nội dung / metric theo ngày / sự kiện ───────────────────────────────────────────

@dataclass(frozen=True)
class ChildSpec:
    kind: str
    table: str
    natural_col: str
    natural_sql_type: str
    date_col: str
    id_prefix: str
    natural_of: Callable[[dict], Any]
    promoted_of: Callable[[dict], dict]
    data_of: Callable[[dict], dict]
    immutable: bool = False


def _d(value: str) -> date:
    return date.fromisoformat(value)


SPECS: dict[str, ChildSpec] = {
    "content": ChildSpec(
        "content", "content_items", "external_id", "text", "published_date", "cnt",
        natural_of=lambda p: p["external_id"],
        promoted_of=lambda p: {"published_date": _d(p["published_date"]), "state": p["state"]},
        data_of=lambda p: {k: v for k, v in p.items()
                           if k not in ("external_id", "resource_external_id", "published_date", "observed_at")}),
    "metrics": ChildSpec(
        "metrics", "daily_metrics", "metric_date", "date", "metric_date", "met",
        natural_of=lambda p: _d(p["date"]),
        promoted_of=lambda p: {},
        data_of=lambda p: {k: v for k, v in p.items() if k not in ("resource_external_id", "date", "observed_at")}),
    "events": ChildSpec(
        "events", "activity_events", "external_id", "text", "occurred_date", "evt",
        natural_of=lambda p: p["external_id"],
        promoted_of=lambda p: {"kind": p["kind"], "occurred_date": _d(p["occurred_date"])},
        data_of=lambda p: {k: v for k, v in p.items() if k not in ("external_id", "resource_external_id", "observed_at")},
        immutable=True),
}


async def fetch_children(conn: asyncpg.Connection, spec: ChildSpec, pairs: list[tuple[str, Any]]) -> dict[tuple[str, Any], dict]:
    """Bản ghi canonical hiện có theo (resource_id, khoá tự nhiên)."""
    if not pairs:
        return {}
    rows = await conn.fetch(
        f"SELECT * FROM {spec.table} WHERE (resource_id, {spec.natural_col}) IN "
        f"(SELECT * FROM unnest($1::text[], $2::{spec.natural_sql_type}[]))",
        [p[0] for p in pairs], [p[1] for p in pairs])
    return {(r["resource_id"], r[spec.natural_col]): dict(r) for r in rows}


async def merge_children(conn: asyncpg.Connection, spec: ChildSpec, *, owner: str, client_id: str, platform: str,
                         items: list[Staged], resource_ids: dict[str, str], snapshot_id: str | None,
                         now: datetime) -> MergeStats:
    from manager_contracts import ErrorCode

    from integration_service.errors import ApiProblem

    stats = MergeStats()
    missing = sorted({s.payload["resource_external_id"] for s in items} - set(resource_ids))
    if missing:  # staging đã chặn RESOURCE_UNKNOWN; tới đây mà thiếu nghĩa là kho thay đổi giữa chừng
        raise ApiProblem(ErrorCode.SNAPSHOT_INCOMPLETE, "Có bản ghi tham chiếu resource không tồn tại", status_code=422,
                         details={"resource_unknown": missing[:20]})
    pairs = [(resource_ids[s.payload["resource_external_id"]], spec.natural_of(s.payload)) for s in items]
    existing = await fetch_children(conn, spec, pairs)
    plan: list[tuple[str, Staged, dict | None, dict, tuple]] = []
    for s, pair in zip(items, pairs, strict=True):
        row = existing.get(pair)
        data = spec.data_of(s.payload)
        verdict = decide(row, s.observed_at, data, immutable=spec.immutable)
        if verdict == "stale":
            stats.stale_ignored += 1
            stats.present_ids.append(row["id"])  # type: ignore[index]
        elif verdict == "unchanged":
            stats.unchanged += 1
            stats.present_ids.append(row["id"])  # type: ignore[index]
            if not spec.immutable and s.observed_at != row["observed_at"]:  # type: ignore[index]
                await conn.execute(f"UPDATE {spec.table} SET observed_at = $2 WHERE id = $1",
                                   row["id"], s.observed_at)  # type: ignore[index]
        else:
            plan.append((verdict, s, row, data, pair))
    if not plan:
        return stats
    revs = await outbox.next_revisions(conn, len(plan))
    stats.revisions += revs
    for (verdict, s, row, data, pair), rev in zip(plan, revs, strict=True):
        promoted = spec.promoted_of(s.payload)
        if verdict == "created":
            cid = new_id(spec.id_prefix)
            cols = ["id", "owner_account_id", "client_id", "platform", "resource_id", spec.natural_col, *promoted,
                    "observed_at", "data", "removed", "revision", "last_snapshot_id", "created_at", "updated_at"]
            values = [cid, owner, client_id, platform, pair[0], pair[1], *promoted.values(), s.observed_at, data,
                      False, rev, snapshot_id, now, now]
            marks = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
            await conn.execute(f"INSERT INTO {spec.table} ({', '.join(cols)}) VALUES ({marks})", *values)
            stats.created += 1
        else:
            cid = row["id"]  # type: ignore[index]
            sets = ["client_id", *promoted, "observed_at", "data", "removed", "revision", "last_snapshot_id", "updated_at"]
            values = [client_id, *promoted.values(), s.observed_at, data, False, rev, snapshot_id, now]
            assign = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(sets))
            await conn.execute(f"UPDATE {spec.table} SET {assign} WHERE id = $1", cid, *values)
            stats.updated += 1
        stats.present_ids.append(cid)
    return stats


# ── Suy luận `removed` (snapshot full + scope) ─────────────────────────────────────

async def infer_removed_resources(conn: asyncpg.Connection, *, owner: str, client_id: str, platform: str,
                                  present_ids: list[str], stats: MergeStats) -> int:
    rows = await conn.fetch(
        "SELECT id FROM resources WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 AND NOT removed "
        "AND NOT (id = ANY($4::text[]))", owner, client_id, platform, present_ids)
    return await _mark_removed(conn, "resources", [r["id"] for r in rows], stats)


async def infer_removed_children(conn: asyncpg.Connection, spec: ChildSpec, *, owner: str, client_id: str, platform: str,
                                 window: tuple[date, date], present_ids: list[str], stats: MergeStats) -> int:
    rows = await conn.fetch(
        f"SELECT id FROM {spec.table} WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 AND NOT removed "
        f"AND {spec.date_col} BETWEEN $4 AND $5 AND NOT (id = ANY($6::text[]))",
        owner, client_id, platform, window[0], window[1], present_ids)
    return await _mark_removed(conn, spec.table, [r["id"] for r in rows], stats)


async def _mark_removed(conn: asyncpg.Connection, table: str, ids: list[str], stats: MergeStats) -> int:
    if not ids:
        return 0
    revs = await outbox.next_revisions(conn, len(ids))
    stats.revisions += revs
    await conn.executemany(f"UPDATE {table} SET removed = true, revision = $2 WHERE id = $1",
                           list(zip(ids, revs, strict=True)))
    return len(ids)


# ── Quan hệ staging (đọc) ─────────────────────────────────────────────────────────

def load_staged(rows: list[asyncpg.Record]) -> list[Staged]:
    out = []
    for r in rows:
        payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        out.append(Staged(r["idx"] or 0, r["item_key"], r["external_id"] or "", r["observed_at"], payload))
    return out
