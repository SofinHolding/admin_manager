"""Canonical Export (Integration -> Analytics): trạng thái canonical đã commit, phân trang theo `revision`.

Analytics KHÔNG đọc DB Integration và không đọc payload thô. Mọi bản ghi ở đây chỉ có thể sinh ra từ snapshot đã commit
(hoặc upsert trực tiếp resource/project): staging và snapshot building/failed/abandoned nằm ở bảng khác nên không bao giờ lộ ra.
"""

from __future__ import annotations

import asyncpg

KINDS = ("projects", "resources", "content", "metrics", "events")
_TABLE = {"projects": "projects", "resources": "resources", "content": "content_items",
          "metrics": "daily_metrics", "events": "activity_events"}


def _base(row: dict) -> dict:
    return {"id": row["id"], "revision": row["revision"], "removed": row["removed"]}


def _project(r: dict) -> dict:
    return {**_base(r), "owner_account_id": r["owner_account_id"], "client_id": r["client_id"],
            "project_key": r["project_key"], "platform": r["platform"], "name": r["name"],
            "country_code": r["country_code"], "enabled": r["enabled"], "metadata": r["metadata"]}


def _resource(r: dict) -> dict:
    d = r["data"]
    projects = d.get("projects") or []
    return {**_base(r), "owner_account_id": r["owner_account_id"], "client_id": r["client_id"],
            "platform": r["platform"], "resource_type": r["resource_type"], "external_id": r["external_id"],
            "state": r["state"], "observed_at": r["observed_at"], "display_name": d.get("display_name"),
            "username": d.get("username"), "url": d.get("url"), "country": d.get("country"),
            "managed": d.get("managed"), "active": d.get("active"), "extensions": d.get("extensions", {}),
            "projects": projects, "project_keys": [p["project_key"] for p in projects],
            "execution_mode": r["execution_mode"], "origin": r["origin"]}


def _content(r: dict) -> dict:
    d = r["data"]
    return {**_base(r), "owner_account_id": r["owner_account_id"], "client_id": r["client_id"],
            "platform": r["platform"], "resource_id": r["resource_id"], "resource_type": r["resource_type"],
            "resource_external_id": r["resource_external_id"], "external_id": r["external_id"],
            "published_date": r["published_date"], "published_at": d.get("published_at"), "url": d.get("url"),
            "state": r["state"], "metrics": d.get("metrics", {}), "extensions": d.get("extensions", {}),
            "observed_at": r["observed_at"]}


def _metric(r: dict) -> dict:
    d = r["data"]
    return {**_base(r), "owner_account_id": r["owner_account_id"], "client_id": r["client_id"],
            "platform": r["platform"], "resource_id": r["resource_id"], "resource_type": r["resource_type"],
            "resource_external_id": r["resource_external_id"], "date": r["metric_date"],
            "metrics": d.get("metrics", {}), "extensions": d.get("extensions", {}), "observed_at": r["observed_at"]}


def _event(r: dict) -> dict:
    d = r["data"]
    return {**_base(r), "owner_account_id": r["owner_account_id"], "client_id": r["client_id"],
            "platform": r["platform"], "resource_id": r["resource_id"], "resource_type": r["resource_type"],
            "resource_external_id": r["resource_external_id"], "external_id": r["external_id"], "kind": r["kind"],
            "occurred_date": r["occurred_date"], "occurred_at": d.get("occurred_at"), "project_key": d.get("project_key"),
            "extensions": d.get("extensions", {}), "observed_at": r["observed_at"]}


_SHAPE = {"projects": _project, "resources": _resource, "content": _content, "metrics": _metric, "events": _event}
_SELECT = {
    "projects": "SELECT t.* FROM projects t",
    "resources": "SELECT t.* FROM resources t",
    "content": "SELECT t.*, r.external_id AS resource_external_id, r.resource_type AS resource_type FROM content_items t "
               "JOIN resources r ON r.id = t.resource_id",
    "metrics": "SELECT t.*, r.external_id AS resource_external_id, r.resource_type AS resource_type FROM daily_metrics t "
               "JOIN resources r ON r.id = t.resource_id",
    "events": "SELECT t.*, r.external_id AS resource_external_id, r.resource_type AS resource_type FROM activity_events t "
              "JOIN resources r ON r.id = t.resource_id",
}


async def head_revision(conn: asyncpg.Connection) -> int:
    """Revision lớn nhất ĐÃ COMMIT trên mọi bảng canonical."""
    parts = " UNION ALL ".join(f"SELECT max(revision) AS r FROM {t}" for t in _TABLE.values())
    return int(await conn.fetchval(f"SELECT COALESCE(max(r), 0) FROM ({parts}) x"))


async def export(conn: asyncpg.Connection, kind: str, after_revision: int, limit: int) -> dict:
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        rows = await conn.fetch(f"{_SELECT[kind]} WHERE t.revision > $1 ORDER BY t.revision, t.id LIMIT $2",
                                after_revision, limit + 1)
        head = await head_revision(conn)
    has_more = len(rows) > limit
    items = [_SHAPE[kind](dict(r)) for r in rows[:limit]]
    return {"items": items, "next_revision": items[-1]["revision"] if items else after_revision,
            "has_more": has_more, "head": head}




# Biểu thức SQL của MỘT dòng checksum cho từng kind — PHẢI trùng `analytics_service.readmodels.canonical.checksum_line`:
#   <khoá tự nhiên>|<revision>|<0/1 removed>[|<số liệu hiển thị>]   (null/vắng viết `n`; phần tử khoá vắng viết rỗng)
# Khoá tự nhiên (bản ghi con lấy resource_type/external_id từ resource cha `r`):
#   projects : owner|project_key
#   resources: owner|platform|resource_type|external_id
#   content  : owner|platform|resource_type|resource_external_id|external_id  + |views,reactions,comments
#   metrics  : owner|platform|resource_type|resource_external_id|YYYY-MM-DD   + |views
#   events   : owner|platform|resource_type|resource_external_id|external_id
_RM = "(CASE WHEN t.removed THEN 1 ELSE 0 END)"


def _m(name: str) -> str:
    return f"COALESCE(t.data->'metrics'->>'{name}', 'n')"


_LINE = {
    "projects": f"format('%s|%s|%s|%s', t.owner_account_id, t.project_key, t.revision, {_RM})",
    "resources": f"format('%s|%s|%s|%s|%s|%s', t.owner_account_id, t.platform, t.resource_type, t.external_id, "
                 f"t.revision, {_RM})",
    "content": f"format('%s|%s|%s|%s|%s|%s|%s|%s,%s,%s', t.owner_account_id, t.platform, r.resource_type, "
               f"r.external_id, t.external_id, t.revision, {_RM}, {_m('views')}, {_m('reactions')}, {_m('comments')})",
    "metrics": f"format('%s|%s|%s|%s|%s|%s|%s|%s', t.owner_account_id, t.platform, r.resource_type, r.external_id, "
               f"to_char(t.metric_date, 'YYYY-MM-DD'), t.revision, {_RM}, {_m('views')})",
    "events": f"format('%s|%s|%s|%s|%s|%s|%s', t.owner_account_id, t.platform, r.resource_type, r.external_id, "
              f"t.external_id, t.revision, {_RM})",
}
_FROM = {kind: f"{_TABLE[kind]} t" + ("" if kind in ("projects", "resources") else " JOIN resources r ON r.id = t.resource_id")
         for kind in KINDS}

CHECKSUM_ALGORITHM = ("sha256(join(LF, sorted(<natural_key>|<revision>|<removed 0/1>[|<displayed metrics>]))); "
                      "natural_key includes resource_type; sort is bytewise (COLLATE C); null is written as n")


async def summary(conn: asyncpg.Connection) -> dict:
    """Số lượng + checksum từng kind để Analytics đối soát (xem `_LINE`; hợp đồng: docs/api/internal-feed-contract-v1.md).

    checksum[kind] = sha256 hex của các dòng `<khoá tự nhiên>|<revision>|<removed 0/1>` (+ `|views,reactions,comments` cho
    content, `|views` cho metrics), sắp theo byte (COLLATE "C"), nối bằng LF; chuỗi rỗng nếu chưa có bản ghi. Khoá tự nhiên
    là khoá của Analytics (KHÔNG phải id nội bộ) nên hai bên tính độc lập ra cùng kết quả."""
    counts: dict[str, dict] = {}
    checksum: dict[str, str] = {}
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        for kind in KINDS:
            row = await conn.fetchrow(
                "SELECT count(*) AS total, count(*) FILTER (WHERE NOT l.removed) AS active, "
                "encode(sha256(convert_to(COALESCE(string_agg(l.line, E'\\n' ORDER BY l.line COLLATE \"C\"), ''), 'UTF8')), "
                f"'hex') AS cs FROM (SELECT t.removed, {_LINE[kind]} AS line FROM {_FROM[kind]}) l")
            counts[kind] = {"total": row["total"], "active": row["active"]}
            checksum[kind] = row["cs"]
        head = await head_revision(conn)
    return {"head_revision": head, "counts": counts, "checksum": checksum, "checksum_algorithm": CHECKSUM_ALGORITHM}
