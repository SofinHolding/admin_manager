"""Trục ngày dùng chung (THUẦN, chỉ stdlib) — cùng hành vi `stats_core.days_between` của legacy."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

MIN_YEAR, MAX_YEAR = 2000, 2100     # ngày ngoài khoảng này không có nghĩa nghiệp vụ (và year 1/9999 làm tràn số học ngày)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_RANGE_DAYS = 400
DEFAULT_RANGE_DAYS = 30


def days_between(date_from: str, date_to: str, *, max_days: int | None = MAX_RANGE_DAYS) -> list[str]:
    """Danh sách ngày `YYYY-MM-DD` trong khoảng (inclusive). Khoảng đảo hoặc dài hơn `max_days` → rỗng (không tự đảo)."""
    try:
        a, b = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return []
    if b < a or (max_days is not None and (b - a).days > max_days):
        return []
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def previous_period(date_from: str, date_to: str) -> tuple[str, str]:
    """Kỳ liền trước có cùng số ngày (như `Db.summary` của legacy). Khoảng rỗng → kỳ trước cũng rỗng."""
    n = len(days_between(date_from, date_to))
    d0 = date.fromisoformat(date_from)
    return (d0 - timedelta(days=n)).isoformat(), (d0 - timedelta(days=1)).isoformat()


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_date(value: str) -> date | None:
    """CHỈ nhận `YYYY-MM-DD` (ASCII) trong [MIN_YEAR, MAX_YEAR]. `fromisoformat` còn nhận `20261006`, `2026-W41-1`...
    — dạng phụ làm phân mảnh khoá cache và lọt qua kiểm tra — nên dùng regex chặt trước."""
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value) or not value.isascii():
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if MIN_YEAR <= parsed.year <= MAX_YEAR else None


def parse_ts(value) -> datetime | None:
    """RFC 3339 (có thể kết thúc bằng Z) -> datetime UTC; sai định dạng -> None."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
