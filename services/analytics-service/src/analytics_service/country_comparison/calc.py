"""So sánh quốc gia (biểu đồ RIÊNG) — port từ `stats_core.views_by_country` / `country_tags`."""

from __future__ import annotations

from analytics_service.dates import days_between
from analytics_service.readmodels.aggregate import content_views_by_entity_day, daily_views_by_entity_day
from analytics_service.readmodels.dataset import KHAC, Dataset, Selection


def views_by_country(ds: Dataset, sel: Selection, date_from: str, date_to: str) -> dict:
    """View mỗi ngày phân bổ theo quốc gia hiệu lực ("Khác" khi trống). `countries` sắp theo bảng chữ cái."""
    days = days_between(date_from, date_to)
    by_day: dict[str, dict[str, int]] = {d: {} for d in days}
    countries: set[str] = set()
    for source in (daily_views_by_entity_day(ds, days, sel), content_views_by_entity_day(ds, days, sel)):
        for ek, day_views in source.items():
            e = ds.entities.get(ek)
            cname = (e.country if e else "") or KHAC
            countries.add(cname)
            for d, v in day_views.items():
                by_day[d][cname] = by_day[d].get(cname, 0) + v
    totals: dict[str, int] = {}
    for day_data in by_day.values():
        for cname, v in day_data.items():
            totals[cname] = totals.get(cname, 0) + v
    return {"countries": sorted(countries), "days": [{"date": d, **by_day[d]} for d in days], "totals": totals}


def country_tags(ds: Dataset, sel: Selection) -> list[str]:
    return sorted({e.country for e in ds.entities.values() if e.country and sel.allows(e.key)})
