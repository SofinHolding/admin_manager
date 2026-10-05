"""Dựng ứng dụng identity-service: chassis chuẩn + router tương thích contract legacy.

CORS không đặt ở đây: thuộc về gateway ("CORS chung"). Service chỉ nhận request từ gateway.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from manager_observability import create_service_app

from identity_service import SERVICE_NAME, __version__, migrate
from identity_service.accounts.router import me_router as accounts_me_router, router as users_router
from identity_service.accounts.service import AccountAdminService
from identity_service.auth.ratelimit import RateLimiter
from identity_service.auth.router import router as auth_router
from identity_service.auth.service import AuthService
from identity_service.config import Settings, load_settings, require_serving_config
from identity_service.container import Container
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.feed_consumer.consumer import IntegrationFeedConsumer
from identity_service.notifications.adapters import build_mailer
from identity_service.oidc.admin_router import router as oidc_admin_router
from identity_service.oidc.backchannel import BackchannelWorker
from identity_service.oidc.clients import ClientService
from identity_service.oidc.router import router as oidc_router
from identity_service.oidc.service import OidcService
from identity_service.notifications.port import Mailer
from identity_service.outbox.router import router as feed_router
from identity_service.permissions.router import router as permissions_router
from identity_service.permissions.service import PermissionService
from identity_service.tokens.keys import KeyStore
from identity_service.tokens.router import router as jwks_router
from identity_service.tokens.service import TokenService
from identity_service.registration_keys.router import router as keys_router
from identity_service.registration_keys.service import RegistrationKeyService
from identity_service.security import utcnow

logger = logging.getLogger("identity.app")

# Dependency (DB) tạm chết: trả 503 thay vì 500, và KHÔNG làm sập tiến trình.
_DB_DOWN = (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError, TimeoutError,
            asyncio.TimeoutError)


def create_app(settings: Settings | None = None, *, database: Database | None = None,
               mailer: Mailer | None = None, now: Callable[[], datetime] = utcnow,
               sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> FastAPI:
    settings = settings or load_settings()
    require_serving_config(settings)  # thiếu cấu hình bắt buộc -> fail-fast lúc khởi động
    owns_db = database is None
    db = database or Database(settings.database_url)

    async def database_ready() -> bool:
        try:
            async with db.connection() as conn:
                return await migrate.is_up_to_date(conn)
        except Exception:  # noqa: BLE001 — DB chết/chưa migrate = chưa sẵn sàng
            return False

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if owns_db:
            await db.open()  # pool lười (min_size=0): không kết nối ngay nên DB chết lúc boot không làm sập
        # Worker nền (asyncio task): lỗi bên trong chỉ ghi log, không bao giờ làm sập tiến trình.
        background: list[asyncio.Task] = []
        container = app.state.container
        if settings.backchannel_enabled and container.keystore is not None:
            background.append(asyncio.create_task(container.backchannel.run_forever(), name="oidc-backchannel"))
        if container.feed_consumer.enabled:
            background.append(asyncio.create_task(container.feed_consumer.run_forever(), name="integration-feed"))
        yield
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if owns_db:
            await db.close()

    app = create_service_app(service_name=SERVICE_NAME, service_version=__version__,
                             readiness_checks={"database": database_ready}, error_style="legacy",
                             lifespan=lifespan)

    resolved_mailer = mailer or build_mailer(settings)
    keystore = (KeyStore(db, settings.key_encryption_key, grace_seconds=settings.jwks_grace_seconds)
                if settings.access_token_alg == "RS256" else None)
    tokens = TokenService(settings, keystore)
    auth_service = AuthService(db, settings, resolved_mailer, tokens, now=now, sleep=sleep)
    app.state.container = Container(
        settings=settings, db=db, auth=auth_service,
        oidc=OidcService(db, settings, auth_service, tokens, now=now),
        oidc_clients=ClientService(db, settings.env, now=now),
        backchannel=BackchannelWorker(db, settings, tokens, now=now),
        feed_consumer=IntegrationFeedConsumer(db, settings, now=now),
        keys=RegistrationKeyService(db, now=now),
        accounts_admin=AccountAdminService(db, now=now),
        permissions=PermissionService(db, now=now), tokens=tokens, keystore=keystore,
        limiter=RateLimiter())

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    async def _db_down(_: Request, exc: Exception) -> JSONResponse:
        logger.warning("Database không khả dụng: %s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Dịch vụ tạm thời không khả dụng"})

    for error_type in _DB_DOWN:  # Starlette nhận từng lớp lỗi, không nhận tuple
        app.add_exception_handler(error_type, _db_down)

    @app.exception_handler(asyncpg.DataError)
    async def _bad_data(_: Request, exc: Exception) -> JSONResponse:
        # Đầu vào mà Postgres không chấp nhận (vd ký tự NUL): lỗi của người gọi, không phải lỗi máy chủ.
        logger.info("Dữ liệu không hợp lệ: %s", type(exc).__name__)
        return JSONResponse(status_code=422, content={"detail": "Dữ liệu không hợp lệ"})

    app.include_router(auth_router)
    app.include_router(keys_router)
    app.include_router(users_router)
    app.include_router(accounts_me_router)
    app.include_router(oidc_router)
    app.include_router(oidc_admin_router)
    app.include_router(permissions_router)
    app.include_router(jwks_router)
    app.include_router(feed_router)
    return app
