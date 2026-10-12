"""Dựng ứng dụng analytics-service: chassis chuẩn + router của các module + consumer nền.

- Đường dẫn MỚI `/v1/analytics/*`: lỗi dạng envelope. Alias LEGACY `/v1/viewer/*`, `/v1/admin/*`: lỗi `{"detail": ...}`.
- `/health/ready` chỉ kiểm DB của CHÍNH service (+ migrate up to date). KHÔNG phụ thuộc Redis hay nguồn feed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from manager_observability import create_service_app
from manager_sdk import FeedClient, TokenVerifier

from analytics_service import SERVICE_NAME, __version__, migrate
from analytics_service.breakdown.router import compare_router
from analytics_service.breakdown.router import router as breakdown_router
from analytics_service.cache import ResultCache
from analytics_service.cache.router import router as cache_router
from analytics_service.config import Settings, load_settings, require_serving_config
from analytics_service.container import Container
from analytics_service.country_comparison.router import router as country_router
from analytics_service.country_config.router import router as country_config_router
from analytics_service.db import Database
from analytics_service.errors import install_error_handlers
from analytics_service.feed_consumer.consumer import FeedConsumer
from analytics_service.feed_consumer.router import router as consumer_router
from analytics_service.freshness.router import router as freshness_router
from analytics_service.groups.router import router as groups_router
from analytics_service.kpi.router import router as kpi_router
from analytics_service.readmodels.query import QueryService
from analytics_service.traffic.router import router as traffic_router

logger = logging.getLogger("analytics.app")

NEW = "/v1/analytics"
LEGACY_VIEWER = "/v1/viewer"
LEGACY_ADMIN = "/v1/admin"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _operation_id(route) -> str:
    # Cùng một handler được gắn dưới nhiều tiền tố (mới + alias legacy) nên operationId phải gồm cả đường dẫn.
    method = sorted(route.methods)[0].lower() if route.methods else "get"
    return re.sub(r"\W+", "_", f"{route.name}_{route.path_format}_{method}").strip("_")


def create_app(settings: Settings | None = None, *, database: Database | None = None,
               cache: ResultCache | None = None, verifier: TokenVerifier | None = None,
               integration_client: FeedClient | None = None, identity_client: FeedClient | None = None,
               now: Callable[[], datetime] = _utcnow, start_consumer: bool | None = None,
               sleep=asyncio.sleep) -> FastAPI:
    settings = settings or load_settings()
    require_serving_config(settings)   # thiếu cấu hình bắt buộc -> fail-fast lúc khởi động
    owns_db = database is None
    db = database or Database(settings.database_url)

    owns_integration = integration_client is None and settings.integration_enabled
    owns_identity = identity_client is None and settings.identity_feed_enabled
    if owns_integration:
        integration_client = FeedClient(settings.integration_url, settings.integration_feed_key, timeout=5.0,
                                        retries=2, backoff_base=settings.consumer_backoff_base_s)
    if owns_identity:
        identity_client = FeedClient(settings.identity_url, settings.identity_feed_key, timeout=5.0, retries=2,
                                     backoff_base=settings.consumer_backoff_base_s)
    result_cache = cache or ResultCache(settings.redis_url, ttl=settings.cache_ttl_s)
    token_verifier = verifier or TokenVerifier(issuer=settings.issuer, audience=settings.audience,
                                               jwks_url=settings.jwks_url)
    consumer = None
    if integration_client is not None or identity_client is not None:
        consumer = FeedConsumer(db, settings, integration=integration_client, identity=identity_client, now=now,
                                sleep=sleep)
    run_consumer = (consumer is not None) if start_consumer is None else (start_consumer and consumer is not None)

    async def database_ready() -> bool:
        try:
            async with db.connection() as conn:
                return await migrate.is_up_to_date(conn)
        except Exception:  # noqa: BLE001 — DB chết/chưa migrate = chưa sẵn sàng
            return False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = None
        if owns_db:
            await db.open()   # pool lười (min_size=0): DB chết lúc boot không làm sập tiến trình
        await token_verifier.prefetch()  # warm-up JWKS: token verify ngay khi service sống, kể cả khi Identity tạm chết
        if run_consumer:
            task = asyncio.create_task(consumer.run_forever(stop), name="analytics-feed-consumer")
        yield
        stop.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for client, owned in ((integration_client, owns_integration), (identity_client, owns_identity)):
            if client is not None and owned:
                await client.aclose()
        await result_cache.close()
        if verifier is None:
            await token_verifier.aclose()
        if owns_db:
            await db.close()

    app = create_service_app(service_name=SERVICE_NAME, service_version=__version__,
                             readiness_checks={"database": database_ready}, lifespan=lifespan,
                             generate_unique_id_function=_operation_id)
    install_error_handlers(app)
    container = Container(settings=settings, db=db, cache=result_cache, verifier=token_verifier, now=now,
                          consumer=consumer)
    container.query = QueryService(db, result_cache, stale_after_s=settings.stale_after_s, now=now)
    app.state.container = container

    for prefix in (NEW, LEGACY_VIEWER):     # dữ liệu: đường dẫn mới + alias legacy cùng shape
        for r in (kpi_router, traffic_router, country_router, breakdown_router, groups_router):
            app.include_router(r, prefix=prefix)
    app.include_router(compare_router, prefix=NEW)
    app.include_router(freshness_router, prefix=NEW)
    for prefix in (NEW + "/admin", LEGACY_ADMIN):
        app.include_router(country_config_router, prefix=prefix)
        app.include_router(cache_router, prefix=prefix)
    app.include_router(consumer_router, prefix=NEW + "/admin")
    return app


__all__ = ["create_app"]
