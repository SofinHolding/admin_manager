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


def resource_data(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in _RESOURCE_KEYS}


async def fetch_resources(conn: asyncpg.Connection, owner: str, platform: str,
                          keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    if not keys:
        return {}
    rows = await conn.fetch(
        "SELECT * FROM resources WHERE owner_account_id = $1 AND platform = $2 "
        "AND (resource_type, external_id) IN (SELECT * FROM unnest($3::text[], $4::text[]))",
        owner, platform, [k[0] for k in keys], [k[1] for k in keys])
    # `projects` (liên kết chủ đề đã gỡ khỏi hợp đồng): bỏ khỏi dữ liệu cũ khi so sánh để không tạo revision giả cho kênh đã có.
    return {(r["resource_type"], r["external_id"]): {**r, "data": {k: v for k, v in r["data"].items() if k != "projects"}}
            for r in map(dict, rows)}


async def resolve_resources(conn: asyncpg.Connection, owner: str, platform: str,
                            external_ids: list[str]) -> tuple[dict[str, str], set[str]]:
    """external_id -> id của resource CÒN SỐNG (chưa removed), và tập external_id MƠ HỒ.

    Bản ghi con (content/metrics/events) chỉ khai `resource_external_id`, không có `resource_type`, nên:
      * resource đã `removed` không được nhận bản ghi con (muốn gắn: gửi lại resource để hồi sinh — hợp đồng ingest §6
        `RESOURCE_UNKNOWN`: "chưa có trong snapshot hay kho");
      * nếu nhiều resource_type còn sống cùng external_id thì KHÔNG đoán (tránh gắn nhầm loại): trả về trong `ambiguous`."""
    if not external_ids:
        return {}, set()
    rows = await conn.fetch(
        "SELECT external_id, array_agg(id ORDER BY id) AS ids FROM resources WHERE owner_account_id = $1 AND platform = $2 "
        "AND external_id = ANY($3::text[]) AND NOT removed GROUP BY external_id", owner, platform, external_ids)
    resolved = {r["external_id"]: r["ids"][0] for r in rows if len(r["ids"]) == 1}
    return resolved, {r["external_id"] for r in rows if len(r["ids"]) > 1}


async def resolve_resource_ids(conn: asyncpg.Connection, owner: str, platform: str,
                               external_ids: list[str]) -> dict[str, str]:
    """external_id -> resource id còn sống và duy nhất (xem `resolve_resources`)."""
    return (await resolve_resources(conn, owner, platform, external_ids))[0]


async def merge_resources(conn: asyncpg.Connection, *, owner: str, client_id: str, platform: str,
                          items: list[Staged], snapshot_id: str | None, now: datetime, origin: str | None = None,
                          execution_mode: str | None = None) -> MergeStats:
    """`origin`/`execution_mode` (tuỳ chọn) đặt cột tương ứng. Legacy ACL truyền `web_manual` khi TẠO resource nhập tay (và
    None khi sửa => giữ nguyên). Đường Tool (snapshot, /resources/batch) PHẢI truyền `origin="tool"`: Tool đẩy một khoá thì
    Tool tiếp quản resource đó (kể cả resource nhập tay trước đó, kể cả khi dữ liệu y hệt) để Legacy (chỉ thấy/sửa
    `origin <> 'tool'`) không còn ghi đè dữ liệu Tool cung cấp."""
    stats = MergeStats()
    keys = [(s.payload["resource_type"], s.payload["external_id"]) for s in items]
    existing = await fetch_resources(conn, owner, platform, keys)
    plan: list[tuple[str, Staged, dict | None, dict]] = []
    for s, key in zip(items, keys, strict=True):
        row = existing.get(key)
        data = resource_data(s.payload)
        verdict = decide(row, s.observed_at, data)
        if verdict == "stale":
            stats.stale_ignored += 1
            stats.present_ids.append(row["id"])  # type: ignore[index]
            stats.outcomes.append((s.index, row["id"], "stale_ignored"))  # type: ignore[index]
        elif verdict == "unchanged" and origin and row["origin"] != origin:  # type: ignore[index]
            plan.append(("updated", s, row, data))   # tiếp quản dù dữ liệu y hệt: đổi origin cần revision + resource.changed
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
                                  present_ids: list[str], stats: MergeStats, as_of: datetime) -> int:
    """R5: vắng mặt trong snapshot full => removed, CHỈ với bản ghi có `observed_at <= as_of` (observed_at của snapshot).
    Bản ghi mới hơn (do batch/delta/legacy ghi trong lúc snapshot đang building) cho thấy Tool còn thấy nó: giữ nguyên."""
    rows = await conn.fetch(
        "SELECT id FROM resources WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 AND NOT removed "
        "AND observed_at <= $5 AND NOT (id = ANY($4::text[]))", owner, client_id, platform, present_ids, as_of)
    return await _mark_removed(conn, "resources", [r["id"] for r in rows], stats)


async def infer_removed_children(conn: asyncpg.Connection, spec: ChildSpec, *, owner: str, client_id: str, platform: str,
                                 window: tuple[date, date], present_ids: list[str], stats: MergeStats,
                                 as_of: datetime) -> int:
    """Như `infer_removed_resources`: chỉ bản ghi trong cửa sổ ngày VÀ có `observed_at <= as_of`."""
    rows = await conn.fetch(
        f"SELECT id FROM {spec.table} WHERE owner_account_id = $1 AND client_id = $2 AND platform = $3 AND NOT removed "
        f"AND {spec.date_col} BETWEEN $4 AND $5 AND observed_at <= $7 AND NOT (id = ANY($6::text[]))",
        owner, client_id, platform, window[0], window[1], present_ids, as_of)
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
