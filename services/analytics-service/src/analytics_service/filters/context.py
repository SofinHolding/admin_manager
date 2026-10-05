"""FilterContext: bộ lọc DUY NHẤT đã qua ép quyền, dùng cho mọi endpoint dữ liệu."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta

from fastapi import Query
from manager_contracts import ApiError, ErrorCode
from manager_sdk import Principal

from analytics_service.dates import DEFAULT_RANGE_DAYS, parse_iso_date


class FilterQuery:
    """Khai báo query-param DUY NHẤT của mọi endpoint dữ liệu (xuất hiện y hệt trong OpenAPI)."""

    def __init__(
        self,
        date_from: str = Query("", description="YYYY-MM-DD (bao gồm); mặc định 30 ngày gần nhất"),
        date_to: str = Query("", description="YYYY-MM-DD (bao gồm); mặc định hôm nay (UTC)"),
        owner_account_id: str = Query("", max_length=64,
                                      description="admin/dash_admin: soi một account; người khác bị ép về chính mình"),
        platform: str = Query("", max_length=40, description="nền tảng (dữ liệu, vd youtube/facebook)"),
        country: str = Query("", max_length=100, description="tên quốc gia (alias → tên chuẩn, không phân biệt hoa/thường)"),
        project: str = Query("", max_length=100, description="project_key (chủ đề/nguồn)"),
        group_id: str = Query("", max_length=64, description="nhóm kênh do viewer tạo"),
        platform_tag: str = Query("", max_length=50,
                                  description="nhãn nền tảng tự do của nhóm kênh (hoặc tên nền tảng gốc)"),
    ) -> None:
        self.date_from = date_from.strip()
        self.date_to = date_to.strip()
        self.owner_account_id = owner_account_id.strip()
        self.platform = platform.strip()
        self.country = country.strip()
        self.project = project.strip()
        self.group_id = group_id.strip()
        self.platform_tag = platform_tag.strip()


@dataclass(frozen=True)
class FilterContext:
    date_from: str
    date_to: str
    owner_account_id: str | None     # None = xuyên owner (chỉ admin/dash_admin không chỉ định owner)
    platform: str = ""
    country: str = ""
    project: str = ""
    group_id: str = ""
    platform_tag: str = ""

    @property
    def scope(self) -> str:
        """Phạm vi người gọi đã phân giải — nằm trong khoá cache để không lộ dữ liệu giữa user."""
        return "all" if self.owner_account_id is None else "own:" + self.owner_account_id

    @property
    def owner_scope(self) -> list[str] | None:
        return None if self.owner_account_id is None else [self.owner_account_id]

    def as_filter(self) -> dict:
        """Bộ lọc ĐÃ ÁP DỤNG (sau khi ép quyền) — trả trong mọi response."""
        return {"date_from": self.date_from, "date_to": self.date_to, "owner_account_id": self.owner_account_id,
                "platform": self.platform, "country": self.country, "project": self.project,
                "group_id": self.group_id, "platform_tag": self.platform_tag}

    def cache_hash(self, extra: dict | None = None) -> str:
        """Băm bộ lọc (KHÔNG gồm owner — owner nằm ở `scope`) + tham số riêng của endpoint (limit, metric...)."""
        body = {k: v for k, v in self.as_filter().items() if k != "owner_account_id"}
        raw = json.dumps({"f": body, "x": extra or {}}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_filter_context(query: FilterQuery, principal: Principal, *, today: date) -> FilterContext:
    """Dựng FilterContext từ query + principal. Đây là NƠI DUY NHẤT quyết định phạm vi owner:

    - admin/dash_admin: xem mọi owner, hoặc owner chỉ định;
    - người khác: luôn bị ép về chính mình (`sub`), bất kể `owner_account_id` gửi lên."""
    date_to = query.date_to or today.isoformat()
    if parse_iso_date(date_to) is None:
        raise ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, "date_to phải có dạng YYYY-MM-DD", status_code=422)
    if query.date_from:
        date_from = query.date_from
        if parse_iso_date(date_from) is None:
            raise ApiError(ErrorCode.REQUEST_VALIDATION_FAILED, "date_from phải có dạng YYYY-MM-DD", status_code=422)
    else:
        date_from = (date.fromisoformat(date_to) - timedelta(days=DEFAULT_RANGE_DAYS - 1)).isoformat()

    if principal.is_admin:
        owner: str | None = query.owner_account_id or None
    else:
        owner = principal.sub
    return FilterContext(date_from=date_from, date_to=date_to, owner_account_id=owner, platform=query.platform,
                         country=query.country, project=query.project, group_id=query.group_id,
                         platform_tag=query.platform_tag)
