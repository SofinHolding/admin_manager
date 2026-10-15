"""Chuỗi traffic theo ngày + top kênh — port từ `stats_core.traffic_metric` / `top_channels` (không import legacy)."""

from __future__ import annotations

from analytics_service.dates import days_between
from analytics_service.readmodels.aggregate import (
    content_by_day,
    content_views_by_entity_day,
    daily_by_day,
    daily_views_by_entity_day,
    videos_by_day,
)
from analytics_service.readmodels.dataset import Dataset, Selection

_ALL_LABEL = "Tổng — cả hai nền tảng"
_DISPLAY = {"youtube": "YouTube", "facebook": "Facebook"}   # chỉ nhãn hiển thị; không ảnh hưởng nghiệp vụ

METRICS = ("views", "interactions", "comments", "videos")


def traffic_metric(ds: Dataset, sel: Selection, date_from: str, date_to: str, country: str) -> dict:
    """views None-aware (None khi cả hai mô hình đều chưa có dữ liệu). interactions/comments CHỈ từ bài đăng (content).
    videos = SỐ BÀI mỗi ngày, fallback đếm event `post_ok` cho ngày chưa có bài."""
    days = days_between(date_from, date_to)
    daily = daily_by_day(ds, days, sel)
    content = content_by_day(ds, days, sel, "views")
    reactions = content_by_day(ds, days, sel, "reactions")
    comments = content_by_day(ds, days, sel, "comments")
    videos_day = videos_by_day(ds, days, sel)

    rows: list[dict] = []
    total_views = 0
    total_videos = 0
    for d in days:
        yt_v, fb_v = daily.get(d), content.get(d)
        views = ((yt_v or 0) + (fb_v or 0)) if (yt_v is not None or fb_v is not None) else None
        videos = videos_day[d]
        rows.append({"date": d, "views": views, "interactions": reactions.get(d) or 0,
                     "comments": comments.get(d) or 0, "videos": videos})
        if views is not None:
            total_views += views
        total_videos += videos

    scope = sel.native_scope
    label = _DISPLAY.get(scope, scope.capitalize()) if scope else _ALL_LABEL
    if country:
        label = f"{label} · {country}"
    return {"scope": scope, "scope_label": label, "country": country, "days": rows,
            "totals": {"views": total_views,
                       "interactions": sum((reactions.get(d) or 0) for d in days),
                       "comments": sum((comments.get(d) or 0) for d in days),
                       "videos": total_videos}}


def top_channels(ds: Dataset, sel: Selection, date_from: str, date_to: str, limit: int) -> dict:
    """Top kênh theo tổng view. Kênh có số liệu theo ngày -> id `<platform>:<external_id>`; kênh có bài đăng -> id
    `<external_id>` (đúng hình dạng legacy: youtube:<id> / <target>)."""
    days = days_between(date_from, date_to)
    items: list[dict] = []
    for ek, by_day in daily_views_by_entity_day(ds, days, sel).items():
        e = ds.entities.get(ek)
        items.append({"id": f"{ek[1]}:{ek[2]}", "name": e.name if e else ek[2], "platform": ek[1],
                      "country": e.country if e else "", "views": sum(by_day.values())})
    for ek, by_day in content_views_by_entity_day(ds, days, sel).items():
        e = ds.entities.get(ek)
        items.append({"id": ek[2], "name": e.name if e else ek[2], "platform": ek[1],
                      "country": e.country if e else "", "views": sum(by_day.values())})
    items.sort(key=lambda x: x["views"], reverse=True)
    return {"items": items[:limit]}
