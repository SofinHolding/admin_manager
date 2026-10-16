"""Kênh theo nền tảng × quốc gia và các phép SO SÁNH theo chiều — port/tổng quát hoá từ legacy."""

from __future__ import annotations

from analytics_service.dates import days_between
from analytics_service.readmodels.aggregate import views_by_entity_day
from analytics_service.readmodels.channels import channel_units
from analytics_service.readmodels.dataset import KHAC, Dataset, Selection

DIMENSIONS = ("platform", "owner", "country", "channel", "group")


def channel_breakdown(ds: Dataset, sel: Selection, custom_tag_refs: dict[str, set[str]]) -> dict:
    """`managed` đếm theo KÊNH: mỗi kênh là một đơn vị.

    `custom_tag_refs`: nhãn tự do (từ Nhóm kênh) -> tập channel_ref, để thêm các dòng `is_custom_tag`."""
    rows: dict[str, dict] = {}
    for e in sorted(ds.entities.values(), key=lambda x: x.key):
        if not sel.allows(e.key):
            continue
        plat = rows.setdefault(e.platform, {"pairs": {}, "meta": {}})
        for u in channel_units(ds, e):
            if not u.managed:
                continue
            key = (e.owner, e.ext_id)
            plat["pairs"][key] = plat["pairs"].get(key, False) or u.active
            plat["meta"][key] = e

    by_platform: list[dict] = []
    total_managed = total_active = 0
    for platform in sorted(rows):
        plat = rows[platform]
        pairs = plat["pairs"]
        managed = len(pairs)
        active = sum(1 for v in pairs.values() if v)
        by_country: dict[str, dict] = {}
        for (owner, ext_id), is_active in pairs.items():
            entity = plat["meta"][(owner, ext_id)]
            cname = entity.country or KHAC
            c = by_country.setdefault(cname, {"managed": 0, "active": 0})
            c["managed"] += 1
            c["active"] += 1 if is_active else 0
        row = {"platform": platform, "managed": managed, "active": active,
               "by_country": [{"country": k, **v} for k, v in sorted(by_country.items())]}
        by_platform.append(row)
        total_managed += managed
        total_active += active

    for tag in sorted(custom_tag_refs):
        refs = custom_tag_refs[tag]
        if sel.keys is not None:
            allowed = {ds.entities[k].ext_id for k in sel.keys if k in ds.entities}
            refs = refs & allowed
        by_platform.append({"platform": tag, "managed": len(refs), "active": len(refs), "is_custom_tag": True})
    return {"total": {"managed": total_managed, "active": total_active}, "by_platform": by_platform}


# ── So sánh theo chiều ──────────────────────────────────────────────────────────

def _series(days: list[str], by_day: dict[str, int]) -> tuple[list[dict], int]:
    series = [{"date": d, "views": by_day.get(d)} for d in days]   # None ≠ 0: ngày chưa có dữ liệu = null
    return series, sum(by_day.values())


def compare(ds: Dataset, sel: Selection, date_from: str, date_to: str, dimension: str, limit: int) -> dict:
    """Mỗi mục: `{id, name, series:[{date, views}], total}`. Một kênh thuộc nhiều nhóm được tính ở từng mục."""
    days = days_between(date_from, date_to)
    per_entity = views_by_entity_day(ds, days, sel)
    buckets: dict[str, dict] = {}

    def add(bid: str, name: str, ek, extra: dict | None = None) -> None:
        b = buckets.setdefault(bid, {"id": bid, "name": name, "by_day": {}, **(extra or {})})
        for d, v in per_entity.get(ek, {}).items():
            b["by_day"][d] = b["by_day"].get(d, 0) + v

    for ek in per_entity:
        e = ds.entities.get(ek)
        if dimension == "platform":
            add(ek[1], ek[1], ek)
        elif dimension == "owner":
            acc = ds.accounts.get(ek[0])
            if acc and acc.get("deleted"):
                continue   # tài khoản đã xoá: ẩn khỏi so sánh người (suspended thì vẫn giữ)
            add(ek[0], (acc or {}).get("username") or ek[0], ek)
        elif dimension == "country":
            cname = (e.country if e else "") or KHAC
            add(cname, cname, ek)
        elif dimension == "channel":
            add(f"{ek[1]}:{ek[2]}", e.name if e else ek[2], ek, {"platform": ek[1]})
        elif dimension == "group":
            for g in ds.groups.values():
                if ds.group_applies(g, ek[0], ek[2]):
                    add(g.id, g.name, ek, {"owner_account_id": g.account_id})

    items = []
    for b in buckets.values():
        series, total = _series(days, b.pop("by_day"))
        items.append({**b, "series": series, "total": total})
    items.sort(key=lambda x: (-x["total"], x["name"], x["id"]))
    return {"dimension": dimension, "days": days, "items": items[:limit]}
