"""Schema pydantic của hợp đồng ingest `2026-10-01` (packages/contracts/ingest/2026-10-01/ingest.schema.json).

Tương đương JSON Schema, có test đối chiếu từng ca đúng/sai. `extra="forbid"` ở MỌI object nên trường `owner_*`
(Tool tự khai chủ sở hữu) bị từ chối — ownership chỉ suy ra từ credential.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt,
                      StringConstraints, model_validator)

MAX_BIGINT = 9_223_372_036_854_775_807
MAX_EXTENSIONS_BYTES = 16 * 1024
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ts(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp phải là chuỗi RFC 3339")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("timestamp không hợp lệ") from None
    if parsed.tzinfo is None:
        raise ValueError("timestamp phải có múi giờ")
    return parsed.astimezone(timezone.utc)


def _date(value: Any) -> date:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError("ngày phải dạng YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("ngày không tồn tại") from None


Timestamp = Annotated[datetime, BeforeValidator(_ts), Field(json_schema_extra={"format": "date-time", "type": "string"})]
DateStr = Annotated[date, BeforeValidator(_date), Field(json_schema_extra={"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"})]
Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ExternalId = Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _metric_before(value: Any) -> Any:
    # JSON Schema coi 1.0 là integer; bool thì KHÔNG phải số (True != 1 trong dữ liệu số liệu).
    if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
        return int(value)
    return value


MetricValue = Annotated[StrictInt, BeforeValidator(_metric_before), Field(ge=0, le=MAX_BIGINT)] | None


def _check_metrics(value: dict) -> dict:
    if len(value) > 64:
        raise ValueError("tối đa 64 metric mỗi bản ghi")
    for k in value:
        if not re.match(r"^[a-z][a-z0-9_]{0,63}$", k):
            raise ValueError("khoá metric sai định dạng")
    return value


def _check_extensions(value: dict) -> dict:
    for k, v in value.items():
        if not re.match(r"^[a-z][a-z0-9_]{0,63}$", k) or not isinstance(v, dict):
            raise ValueError("extensions: khoá phải là key hợp lệ và giá trị là object")
    if len(json.dumps(value)) > MAX_EXTENSIONS_BYTES:
        raise ValueError("extensions quá lớn")
    return value


Metrics = Annotated[dict[str, MetricValue], AfterValidator(_check_metrics)]
Extensions = Annotated[dict[str, dict[str, Any]], AfterValidator(_check_extensions)]


class _Strict(BaseModel):
    """Mọi object trong hợp đồng ingest là `additionalProperties:false`.

    Trường tuỳ chọn khai dạng `Kiểu = None`: VẮNG MẶT => None, nhưng `null` tường minh bị từ chối vì schema hợp đồng
    chỉ cho phép `null` ở giá trị metric (xem `MetricValue`)."""

    model_config = ConfigDict(extra="forbid")


class Country(_Strict):
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] = None
    name: Annotated[str, StringConstraints(max_length=100)] = None


class ResourceItem(_Strict):
    external_id: ExternalId
    resource_type: Key
    display_name: Annotated[str, StringConstraints(max_length=200)] = None
    username: Annotated[str, StringConstraints(max_length=200)] = None
    url: Annotated[str, StringConstraints(max_length=500)] = None
    country: Country = None
    state: Literal["active", "disabled"] = "active"
    managed: StrictBool = None
    active: StrictBool = None
    observed_at: Timestamp
    extensions: Extensions = None


class ContentItem(_Strict):
    external_id: ExternalId
    resource_external_id: ExternalId
    published_date: DateStr
    published_at: Timestamp = None
    url: Annotated[str, StringConstraints(max_length=500)] = None
    state: Literal["active", "deleted"] = "active"
    metrics: Metrics
    observed_at: Timestamp
    extensions: Extensions = None


class MetricItem(_Strict):
    resource_external_id: ExternalId
    date: DateStr
    metrics: Metrics
    observed_at: Timestamp
    extensions: Extensions = None


class EventItem(_Strict):
    external_id: ExternalId
    resource_external_id: ExternalId
    kind: Key
    occurred_date: DateStr
    occurred_at: Timestamp = None
    extensions: Extensions = None


class ResourceUpsertItem(ResourceItem):
    """Phần tử của `POST /resources/batch` (ngoài snapshot): thêm `platform`; `observed_at` mặc định = lúc nhận."""
    platform: Key
    observed_at: Timestamp = None
    country_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] = None

    @model_validator(mode="after")
    def _fold_convenience_fields(self) -> "ResourceUpsertItem":
        if self.country_code and self.country is None:
            self.country = Country(code=self.country_code)
        return self


class WindowModel(_Strict):
    # alias `from` là từ khoá Python
    from_: DateStr = Field(alias="from")
    to: DateStr

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _ordered(self) -> "WindowModel":
        if self.from_ > self.to:
            raise ValueError("from phải <= to")
        return self


class Scope(_Strict):
    resources: StrictBool = None
    content_window: WindowModel = None
    metrics_window: WindowModel = None
    events_window: WindowModel = None


class Counts(_Strict):
    resources: Annotated[StrictInt, Field(ge=0)] = None
    content: Annotated[StrictInt, Field(ge=0)] = None
    metrics: Annotated[StrictInt, Field(ge=0)] = None
    events: Annotated[StrictInt, Field(ge=0)] = None


class SnapshotCreate(_Strict):
    platform: Key
    snapshot_type: Literal["full", "delta"]
    observed_at: Timestamp
    report_timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    scope: Scope = None
    expected_counts: Counts

    @model_validator(mode="after")
    def _scope_only_for_full(self) -> "SnapshotCreate":
        if self.scope is not None and self.snapshot_type != "full":
            raise ValueError("scope chỉ có nghĩa khi snapshot_type=full")
        return self


class CommitRequest(_Strict):
    accept_partial: StrictBool = False


class _Batch(_Strict):
    # Chỉ để TÀI LIỆU OpenAPI: runtime kiểm số lượng (BATCH_TOO_LARGE) và từng bản ghi (rejections) thủ công.
    pass


class ResourceBatch(_Batch):
    items: list[ResourceItem] = Field(min_length=1, max_length=500)


class ResourceUpsertBatch(_Batch):
    items: list[ResourceUpsertItem] = Field(min_length=1, max_length=500)


class ContentBatch(_Batch):
    items: list[ContentItem] = Field(min_length=1, max_length=500)


class MetricsBatch(_Batch):
    items: list[MetricItem] = Field(min_length=1, max_length=500)


class EventBatch(_Batch):
    items: list[EventItem] = Field(min_length=1, max_length=500)


ITEM_MODELS = {"resources": ResourceItem, "content": ContentItem, "metrics": MetricItem, "events": EventItem}

# Trường định danh (khoá tự nhiên) của từng loại — phần còn lại là `data`.
KEY_FIELDS = {
    "resources": ("resource_type", "external_id"),
    "content": ("resource_external_id", "external_id"),
    "metrics": ("resource_external_id", "date"),
    "events": ("resource_external_id", "external_id"),
}
