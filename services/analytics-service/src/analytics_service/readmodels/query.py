"""Thực thi một truy vấn dữ liệu: FilterContext -> (cache HIT?) -> dataset -> tính -> cache SET -> thêm filter + freshness.

MỌI endpoint dữ liệu đi qua `QueryService.run` ⇒ cùng một cách dựng FilterContext, cùng cách khoá cache (có scope
người gọi), cùng khối `filter` và `freshness`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Response
from manager_sdk import Principal

from analytics_service.cache import ResultCache, build_key
from analytics_service.dates import days_between, iso_utc, previous_period
from analytics_service.db import Database
from analytics_service.filters import FilterContext, FilterQuery, build_filter_context
from analytics_service.freshness import freshness_block, load_sync_info
from analytics_service.readmodels.dataset import Dataset, Selection, as_of, load_dataset, select_entities


@dataclass(frozen=True)
class QuerySpec:
    endpoint: str                                                   # tên trong khoá cache
    build: Callable[[Dataset, FilterContext, Selection, dict], dict]  # (ds, ctx, sel, extra) -> body
    window: str = "range"                                           # "range" | "prev" | "none"
    ignore_country: bool = False                                    # biểu đồ so sánh quốc gia không bị thu hẹp theo quốc gia


class QueryService:
    def __init__(self, db: Database, cache: ResultCache, *, stale_after_s: int, now: Callable[[], datetime]) -> None:
        self.db = db
        self.cache = cache
        self.stale_after_s = stale_after_s
        self.now = now

    def context(self, query: FilterQuery, principal: Principal) -> FilterContext:
        return build_filter_context(query, principal, today=self.now().date())

    @staticmethod
    def _window(spec: QuerySpec, ctx: FilterContext) -> tuple[str, str] | None:
        if spec.window == "none" or not days_between(ctx.date_from, ctx.date_to):
            return None
        if spec.window == "prev":
            prev_from, _ = previous_period(ctx.date_from, ctx.date_to)
            return min(prev_from, ctx.date_from), ctx.date_to
        return ctx.date_from, ctx.date_to

    async def run(self, spec: QuerySpec, principal: Principal, query: FilterQuery, response: Response,
                  extra: dict | None = None) -> dict:
        ctx = self.context(query, principal)
        extra = extra or {}
        async with self.db.connection() as conn:
            info = await load_sync_info(conn)   # seal đọc từ PostgreSQL TRƯỚC khi tra cache
        key = build_key(info.seal, ctx.scope, spec.endpoint, ctx.cache_hash(extra))
        payload = await self.cache.get(key)
        status = "HIT"
        if payload is None:
            status = "MISS" if self.cache.configured else "OFF"
            window = self._window(spec, ctx)
            async with self.db.connection() as conn:
                ds = await load_dataset(conn, ctx.owner_scope, window, with_rows=window is not None)
            sel = select_entities(ds, ctx, ignore_country=spec.ignore_country)
            body = await asyncio.to_thread(spec.build, ds, ctx, sel, extra)
            filt = ctx.as_filter()
            if not spec.ignore_country:
                filt["country"] = sel.country or ctx.country
            payload = {"body": body, "filter": filt, "as_of": iso_utc(as_of(ds, sel))}
            await self.cache.set(key, payload)
        response.headers["X-Cache"] = status
        fresh = freshness_block(info, _parse(payload["as_of"]), self.now(), self.stale_after_s)
        return {**payload["body"], "filter": payload["filter"], "freshness": fresh}


def _parse(value: str | None) -> datetime | None:
    from analytics_service.dates import parse_ts
    return parse_ts(value)
