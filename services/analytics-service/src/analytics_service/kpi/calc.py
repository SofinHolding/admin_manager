"""KPI tóm tắt — port từ `stats_core.summary` (không import legacy)."""

from __future__ import annotations

from analytics_service.dates import days_between, previous_period
from analytics_service.readmodels.aggregate import content_by_day, daily_by_day, post_ok_events
from analytics_service.readmodels.channels import channel_units
from analytics_service.readmodels.dataset import Dataset, Selection


def _total_views(ds: Dataset, days: list[str], sel: Selection) -> int:
    daily = daily_by_day(ds, days, sel)
    content = content_by_day(ds, days, sel, "views")
    return sum(v for v in daily.values() if v) + sum(v for v in content.values() if v)


def summary(ds: Dataset, sel: Selection, date_from: str, date_to: str, project: str = "") -> dict:
    days = days_between(date_from, date_to)
    total_views = _total_views(ds, days, sel)

    prev_from, prev_to = previous_period(date_from, date_to)
    pdays = days_between(prev_from, prev_to)
    total_prev = _total_views(ds, pdays, sel) if pdays else 0

    videos = len(post_ok_events(ds, sel, set(days), project))
    videos_prev = len(post_ok_events(ds, sel, set(pdays), project)) if pdays else 0

    managed: set = set()
    active: set = set()
    for e in ds.entities.values():
        if not sel.allows(e.key):
            continue
        units = channel_units(ds, e)
        if project:
            units = [u for u in units if u.project_key == project] or units
        if any(u.managed for u in units):
            managed.add(e.key)
        if any(u.active for u in units):
            active.add(e.key)

    return {"total_views": total_views, "total_views_prev": total_prev, "videos_posted": videos,
            "videos_posted_prev": videos_prev, "active_channels": len(active), "managed_channels": len(managed),
            "days_in_range": len(days)}
