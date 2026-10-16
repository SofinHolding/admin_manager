"""Kiểm tra từng bản ghi ingest, chuẩn hoá thành payload JSON và gom `rejections` (R7: không bỏ sót im lặng).

Lỗi cấp REQUEST (body không phải object, thiếu `items`, quá 500, có trường `owner_*`) ném ApiProblem; lỗi cấp BẢN GHI
trả về trong danh sách rejections kèm mã ổn định. Thông điệp lỗi chỉ nêu vị trí/loại — KHÔNG phản chiếu giá trị đầu vào.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from manager_contracts import ErrorCode
from pydantic import ValidationError

from integration_service.config import MAX_BATCH_ITEMS
from integration_service.errors import ApiProblem, invalid
from integration_service.ingest import schemas
from integration_service.security import iso


@dataclass(frozen=True)
class Staged:
    index: int
    item_key: str
    external_id: str
    observed_at: datetime
    payload: dict


@dataclass(frozen=True)
class Rejection:
    index: int
    external_id: str | None
    code: str
    message: str
    item_key: str | None = None   # khoá tự nhiên (nếu suy ra được) để gửi lại đúng thì xoá rejection cũ

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"index": self.index, "code": self.code, "message": self.message}
        if self.external_id is not None:
            out["external_id"] = self.external_id
        return out


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def extract_items(body: dict) -> list[dict]:
    """Lấy `items` từ body và áp giới hạn cấp request."""
    unknown = sorted(set(body) - {"items"})
    owner_top = [k for k in unknown if k.lower().startswith("owner")]
    if owner_top:
        raise owner_spoof(owner_top)
    if unknown:
        raise invalid("Body chỉ được có `items`", {"unexpected": unknown[:5]})
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise invalid("`items` phải là mảng không rỗng")
    if len(items) > MAX_BATCH_ITEMS:
        raise ApiProblem(ErrorCode.BATCH_TOO_LARGE, f"Tối đa {MAX_BATCH_ITEMS} bản ghi mỗi batch", status_code=413,
                         details={"max": MAX_BATCH_ITEMS, "received": len(items)})
    spoofed = sorted({k for it in items if isinstance(it, dict) for k in it if k.lower().startswith("owner")})
    if spoofed:
        raise owner_spoof(spoofed)
    return items


def owner_spoof(fields: list[str]) -> ApiProblem:
    return invalid("Trường owner_* không được gửi: quyền sở hữu suy ra từ credential",
                   {"fields": sorted(fields)[:10]})


def _summarize(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(item)"
        parts.append(f"{loc}: {err.get('type', 'invalid')}")
    return "; ".join(parts)


def raw_key(kind: str, raw: dict) -> str | None:
    values = [raw.get(f) for f in schemas.KEY_FIELDS[kind]]
    if not all(isinstance(v, str) for v in values):
        return None
    return json.dumps(values, ensure_ascii=False)


def _item_key(kind: str, payload: dict) -> str:
    values = [payload[f] for f in schemas.KEY_FIELDS[kind]]
    if kind == "resources" and payload.get("platform"):  # /resources/batch: một batch có thể chứa nhiều platform
        values.insert(0, payload["platform"])
    return json.dumps(values, ensure_ascii=False)


def normalize(kind: str, model: schemas.BaseModel) -> dict:
    """Payload JSON-able, đầy đủ mặc định, ổn định giữa các lần gửi (để so sánh `unchanged`)."""
    raw = jsonable(model.model_dump(mode="python", by_alias=False))
    if kind == "resources":
        country = raw.get("country")
        if country is not None:
            country = {"code": country.get("code"), "name": country.get("name")}
            if country["code"] is None and country["name"] is None:
                country = None
        raw["country"] = country
        raw["extensions"] = raw.get("extensions") or {}
    else:
        raw["extensions"] = raw.get("extensions") or {}
    if kind == "resources":
        raw.pop("country_code", None)   # trường tiện ích của /resources/batch đã gộp vào country
    return raw


def validate_items(kind: str, items: list[Any], *, known_metrics: set[str] | None = None,
                   default_observed_at: datetime | None = None,
                   model: type | None = None) -> tuple[list[Staged], list[Rejection], int]:
    """(hợp lệ đã khử trùng khoá, rejections, số bản ghi hợp lệ bị bản mới hơn CÙNG batch đè)"""
    model = model or schemas.ITEM_MODELS[kind]
    staged: dict[str, Staged] = {}
    rejections: list[Rejection] = []
    order: list[str] = []
    valid = 0
    for index, raw in enumerate(items):
        ext = raw.get("external_id") if isinstance(raw, dict) and isinstance(raw.get("external_id"), str) else None
        if not isinstance(raw, dict):
            rejections.append(Rejection(index, None, ErrorCode.RECORD_VALIDATION_FAILED.value, "Bản ghi phải là object"))
            continue
        if "\\u0000" in json.dumps(raw):
            rejections.append(Rejection(index, ext, ErrorCode.RECORD_VALIDATION_FAILED.value, "Chứa ký tự NUL",
                                        raw_key(kind, raw)))
            continue
        try:
            parsed = model.model_validate(raw)
        except ValidationError as exc:
            rejections.append(Rejection(index, ext, ErrorCode.RECORD_VALIDATION_FAILED.value, _summarize(exc),
                                        raw_key(kind, raw)))
            continue
        payload = normalize(kind, parsed)
        observed = parsed.observed_at if getattr(parsed, "observed_at", None) is not None else default_observed_at
        if kind == "events":
            observed = default_observed_at  # sự kiện bất biến: dùng thời điểm của snapshot
        if observed is None:
            rejections.append(Rejection(index, ext, ErrorCode.RECORD_VALIDATION_FAILED.value, "observed_at: missing",
                                        raw_key(kind, raw)))
            continue
        payload["observed_at"] = iso(observed)
        if kind in ("content", "metrics") and known_metrics is not None:
            unknown = sorted(set(payload["metrics"]) - known_metrics)
            if unknown:
                rejections.append(Rejection(
                    index, ext or payload.get("resource_external_id"), ErrorCode.METRIC_UNKNOWN.value,
                    "Khoá metric chưa có trong registry (đưa vào extensions): " + ", ".join(unknown[:5]),
                    raw_key(kind, raw)))
                continue
        key = _item_key(kind, payload)
        valid += 1
        entry = Staged(index, key, ext or payload.get("resource_external_id") or "", observed, payload)
        previous = staged.get(key)
        if previous is None:
            order.append(key)
            staged[key] = entry
        elif entry.observed_at >= previous.observed_at:  # trong cùng batch: bản mới hơn thắng, hoà thì bản sau
            staged[key] = entry
    return [staged[k] for k in order], rejections, valid - len(staged)
