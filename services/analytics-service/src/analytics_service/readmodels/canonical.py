"""Ánh xạ bản ghi canonical (Integration export) -> hàng read model. THUẦN, không chạm DB.

Trường theo `docs/api/ingest-data-contract-v1.md` mục 4 + `docs/api/internal-feed-contract-v1.md` mục 5.
Bản ghi sai (thiếu khoá, ngày sai, metric âm/không nguyên) ném `BadItem`: consumer ghi dead-letter cho RIÊNG bản ghi đó và
tiếp tục, không chặn cả đợt.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from analytics_service.dates import parse_iso_date, parse_ts

KINDS = ("projects", "resources", "content", "metrics", "events")
LF = chr(10)


class BadItem(ValueError):
    """Bản ghi canonical không hợp lệ (không phải lỗi hạ tầng)."""


def _text(item: dict, name: str, *, required: bool = False, limit: int = 300) -> str:
    value = item.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise BadItem(f"thiếu `{name}`")
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise BadItem(f"`{name}` phải là chuỗi")
    text = str(value).strip()
    if "\x00" in text:
        raise BadItem(f"`{name}` chứa ký tự không hợp lệ")
    return text[:limit]


def _revision(item: dict) -> int:
    rev = item.get("revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise BadItem("`revision` phải là số nguyên >= 0")
    return rev


def _date(item: dict, name: str) -> date:
    raw = item.get(name)
    parsed = parse_iso_date(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise BadItem(f"`{name}` phải có dạng YYYY-MM-DD")
    return parsed


def _metric(metrics: dict, name: str) -> int | None:
    value = metrics.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BadItem(f"metric `{name}` phải là số nguyên >= 0 hoặc null")  # null KHÁC 0
    return value


def _flag(item: dict, name: str) -> bool | None:
    value = item.get(name)
    return value if isinstance(value, bool) else None


def country_name(item: dict) -> str:
    """Quốc gia thô: `{code,name}` ưu tiên name; hoặc chuỗi; hoặc `country_code`."""
    raw = item.get("country")
    if isinstance(raw, dict):
        text = raw.get("name") or raw.get("code") or ""
    elif isinstance(raw, str):
        text = raw
    else:
        text = item.get("country_code") or ""
    return text.strip()[:100] if isinstance(text, str) else ""


def _common(item: dict) -> dict:
    if not isinstance(item, dict):
        raise BadItem("bản ghi không phải object")
    return {"owner_account_id": _text(item, "owner_account_id", required=True),
            "observed_at": parse_ts(item.get("observed_at")), "revision": _revision(item),
            "removed": item.get("removed") is True}


def _bindings(item: dict) -> list[dict]:
    out: dict[str, dict] = {}
    raw = item.get("projects")
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("project_key"):
                key = str(entry["project_key"]).strip()
                out.setdefault(key, {"project_key": key, "enabled": entry.get("enabled", True) is not False})
            elif isinstance(entry, str) and entry.strip():
                out.setdefault(entry.strip(), {"project_key": entry.strip(), "enabled": True})
    if not out and isinstance(item.get("project_keys"), list):
        for key in item["project_keys"]:
            if isinstance(key, str) and key.strip():
                out.setdefault(key.strip(), {"project_key": key.strip(), "enabled": True})
    return list(out.values())


def to_row(kind: str, item: dict) -> dict:
    row = _common(item)
    if kind == "projects":
        row.update(project_key=_text(item, "project_key", required=True, limit=100), name=_text(item, "name"),
                   platform=_text(item, "platform"), country=country_name(item),
                   enabled=item.get("enabled", True) is not False)
    elif kind == "resources":
        row.update(platform=_text(item, "platform", required=True, limit=40),
                   external_id=_text(item, "external_id", required=True, limit=200),
                   resource_type=_text(item, "resource_type", limit=40), client_id=_text(item, "client_id", limit=100),
                   display_name=_text(item, "display_name"), username=_text(item, "username"),
                   url=_text(item, "url", limit=1000), country=country_name(item),
                   state="disabled" if item.get("state") == "disabled" else "active",
                   projects=_bindings(item), managed=_flag(item, "managed"), active=_flag(item, "active"))
    elif kind == "content":
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        row.update(platform=_text(item, "platform", required=True, limit=40),
                   resource_type=_text(item, "resource_type", limit=40),
                   resource_external_id=_text(item, "resource_external_id", required=True, limit=200),
                   external_id=_text(item, "external_id", required=True, limit=300),
                   published_date=_date(item, "published_date"), views=_metric(metrics, "views"),
                   reactions=_metric(metrics, "reactions"), comments=_metric(metrics, "comments"),
                   state="deleted" if item.get("state") == "deleted" else "active")
    elif kind == "metrics":
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        views = _metric(metrics, "views")
        row.update(platform=_text(item, "platform", required=True, limit=40),
                   resource_type=_text(item, "resource_type", limit=40),
                   resource_external_id=_text(item, "resource_external_id", required=True, limit=200),
                   metric_date=_date(item, "date"), views=views,
                   metrics={k: v for k, v in metrics.items() if isinstance(v, int) and not isinstance(v, bool)})
    elif kind == "events":
        row.update(external_id=_text(item, "external_id", required=True, limit=300),
                   platform=_text(item, "platform", limit=40), resource_type=_text(item, "resource_type", limit=40),
                   resource_external_id=_text(item, "resource_external_id", limit=200),
                   kind=_text(item, "kind", required=True, limit=60), occurred_date=_date(item, "occurred_date"),
                   project_key=_text(item, "project_key", limit=100))
    else:
        raise BadItem(f"kind không hỗ trợ: {kind}")
    return row


def natural_key(kind: str, row: dict) -> str:
    """Khoá tự nhiên dạng chuỗi (dùng cho checksum đối soát và dead-letter id), khớp UNIQUE của Integration:
    resources (owner, platform, resource_type, external_id); content/events (…resource_type, resource_external_id,
    external_id); metrics (…resource_type, resource_external_id, date). Phần tử vắng viết rỗng."""
    o = row["owner_account_id"]
    if kind == "projects":
        parts = (o, row["project_key"])
    elif kind == "resources":
        parts = (o, row["platform"], row["resource_type"], row["external_id"])
    elif kind == "content":
        parts = (o, row["platform"], row["resource_type"], row["resource_external_id"], row["external_id"])
    elif kind == "metrics":
        md = row["metric_date"]
        parts = (o, row["platform"], row["resource_type"], row["resource_external_id"],
                 md.isoformat() if isinstance(md, date) else str(md))
    else:
        parts = (o, row["platform"], row["resource_type"], row["resource_external_id"], row["external_id"])
    return "|".join(parts)


def checksum_lines(lines: list[str]) -> str:
    """sha256 hex của các dòng đã sắp xếp (sort theo chuỗi, nối bằng LF, mã hoá UTF-8)."""
    digest = hashlib.sha256()
    digest.update(LF.join(sorted(lines)).encode("utf-8"))
    return digest.hexdigest()


def _n(value) -> str:
    return "n" if value is None else str(value)


def checksum_line(kind: str, row: dict) -> str:
    """Một dòng của checksum đối soát: `<khoá tự nhiên>|<revision>|<0/1 removed>` và, với số liệu hiển thị:
    content `|<views>,<reactions>,<comments>`, metrics `|<views>` (null viết là `n`).

    ĐÂY LÀ THUẬT TOÁN ĐỐI SOÁT: `canonical/summary.checksum[kind]` của Integration phải tính y hệt (xem README)."""
    line = f"{natural_key(kind, row)}|{row['revision']}|{1 if row['removed'] else 0}"
    if kind == "content":
        line += f"|{_n(row['views'])},{_n(row['reactions'])},{_n(row['comments'])}"
    elif kind == "metrics":
        line += f"|{_n(row['views'])}"
    return line


def as_datetime(value) -> datetime | None:
    return value if isinstance(value, datetime) else None
