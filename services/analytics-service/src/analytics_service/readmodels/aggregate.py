"""Tổng hợp theo ngày — PORT RIÊNG của analytics từ `stats_core` (không import legacy).

Hai "mô hình" dữ liệu (theo HÌNH DẠNG dữ liệu, không theo tên nền tảng):
- content (bài đăng): lượt xem của MỘT NGÀY = tổng `views` của các bài có NGÀY ĐĂNG là ngày đó. `views` là số cộng dồn
  hiện tại ⇒ cập nhật bài cũ làm đổi số của ngày đăng cũ.
- daily (số liệu theo ngày của kênh): lượt xem của ngày = `views` của ngày đó.

`None` ≠ `0`: ngày chưa có dữ liệu trả `None`; có dữ liệu bằng 0 trả `0`.
"""

from __future__ import annotations

from analytics_service.readmodels.dataset import Dataset, EntityKey, Selection


def content_by_day(ds: Dataset, days: list[str], sel: Selection, field: str | None) -> dict[str, int | None]:
    """`field=None` → ĐẾM bài; ngược lại cộng `field` (views|reactions|comments). `None` cho ngày không có bài."""
    out: dict[str, int | None] = {d: None for d in days}
    for p in ds.content:
        if not sel.allows(p.ek):
            continue
        day = p.date
        if day not in out:
            continue
        if field is None:
            out[day] = (out[day] or 0) + 1
        else:
            v = getattr(p, field)
            if v is not None:
                out[day] = (out[day] or 0) + int(v)
    return out


def daily_by_day(ds: Dataset, days: list[str], sel: Selection) -> dict[str, int | None]:
    out: dict[str, int | None] = {d: None for d in days}
    for rec in ds.daily:
        if rec.date not in out or not sel.allows(rec.ek):
            continue
        if rec.views is None:
            continue
        out[rec.date] = (out[rec.date] or 0) + int(rec.views)
    return out


def content_views_by_entity_day(ds: Dataset, days: list[str], sel: Selection) -> dict[EntityKey, dict[str, int]]:
    valid = set(days)
    out: dict[EntityKey, dict[str, int]] = {}
    for p in ds.content:
        if p.date not in valid or not sel.allows(p.ek):
            continue
        by_day = out.setdefault(p.ek, {})
        by_day[p.date] = by_day.get(p.date, 0) + int(p.views or 0)
    return out


def daily_views_by_entity_day(ds: Dataset, days: list[str], sel: Selection) -> dict[EntityKey, dict[str, int]]:
    valid = set(days)
    out: dict[EntityKey, dict[str, int]] = {}
    for rec in ds.daily:
        if rec.date not in valid or rec.views is None or not sel.allows(rec.ek):
            continue
        by_day = out.setdefault(rec.ek, {})
        by_day[rec.date] = by_day.get(rec.date, 0) + int(rec.views)
    return out


def views_by_entity_day(ds: Dataset, days: list[str], sel: Selection) -> dict[EntityKey, dict[str, int]]:
    """Gộp hai mô hình: {kênh: {ngày: lượt xem}} (dùng cho compare/*)."""
    merged: dict[EntityKey, dict[str, int]] = {}
    for source in (content_views_by_entity_day(ds, days, sel), daily_views_by_entity_day(ds, days, sel)):
        for ek, by_day in source.items():
            target = merged.setdefault(ek, {})
            for d, v in by_day.items():
                target[d] = target.get(d, 0) + v
    return merged


def post_ok_events(ds: Dataset, sel: Selection, days: set[str], project: str = "") -> list:
    out = []
    for e in ds.events:
        if e.kind != "post_ok" or e.date not in days or not sel.allows(e.ek):
            continue
        if project and e.project_key and e.project_key != project:
            continue
        out.append(e)
    return out

