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
from identity_service.accounts.router import router as users_router
from identity_service.accounts.service import AccountAdminService
from identity_service.auth.ratelimit import RateLimiter
from identity_service.auth.router import router as auth_router
from identity_service.auth.service import AuthService
from identity_service.config import Settings, load_settings, require_serving_config
from identity_service.container import Container
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.notifications.adapters import build_mailer
from identity_service.notifications.port import Mailer
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
        yield
        if owns_db:
            await db.close()

    app = create_service_app(service_name=SERVICE_NAME, service_version=__version__,
                             readiness_checks={"database": database_ready}, error_style="legacy",
                             lifespan=lifespan)

    resolved_mailer = mailer or build_mailer(settings)
    app.state.container = Container(
        settings=settings, db=db,
        auth=AuthService(db, settings, resolved_mailer, now=now, sleep=sleep),
        keys=RegistrationKeyService(db, now=now),
        accounts_admin=AccountAdminService(db, now=now),
        limiter=RateLimiter())

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                            headers=exc.headers)

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
    return app
