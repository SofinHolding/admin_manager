"""Dựng ứng dụng integration-service: chassis (envelope lỗi kiểu MỚI) + các router theo module.

CORS không đặt ở đây (thuộc gateway). Mặt phẳng: USER (token RS256 qua JWKS), MÁY (API key `mpk_`), NỘI BỘ (`X-Service-Key`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from manager_contracts import ErrorCode, error_envelope
from manager_observability import create_service_app, current_request_id
from manager_sdk import FeedClient, ServiceKeyAuth, TokenVerifier, install_auth_error_handler

from integration_service import SERVICE_NAME, __version__, maintenance, migrate
from integration_service.api_models import ClientPatch, CredentialCreate, RotateRequest
from integration_service.audit.router import router as audit_router
from integration_service.audit.service import AuditService
from integration_service.channel_groups.router import router as channel_groups_router
from integration_service.clients.router import router as clients_router
from integration_service.clients.service import ClientService
from integration_service.config import Settings, load_settings, require_serving_config
from integration_service.container import Container
from integration_service.credentials.router import router as credentials_router
from integration_service.credentials.service import CredentialService
from integration_service.crypto import RevealCrypto
from integration_service.db import Database
from integration_service.errors import ApiProblem
from integration_service.feed.router import build_router as build_feed_router
from integration_service.http_utils import BodyLimitMiddleware, EnvelopeResponse, install_openapi_models
from integration_service.ingest import schemas
from integration_service.guide.router import router as guide_router
from integration_service.guide.service import GuideService
from integration_service.installations.router import router as installations_router
from integration_service.installations.service import HandshakeRequest, InstallationService
from integration_service.jobs import schemas as job_schemas
from integration_service.jobs.router import machine_router as jobs_machine_router
from integration_service.jobs.router import user_router as jobs_user_router
from integration_service.jobs.service import JobService
from integration_service.legacy_acl import errors as legacy_errors
from integration_service.legacy_acl.router import build_routers as build_legacy_routers
from integration_service.legacy_acl.service import LegacyService
from integration_service.machine_runner import MachineRunner
from integration_service.platforms.router import router as platforms_router
from integration_service.projects.router import router as projects_router
from integration_service.projects.service import ProjectService, ProjectUpsert
from integration_service.provisioning.consumer import ProvisioningConsumer
from integration_service.ratelimit import FixedWindowLimiter
from integration_service.resources.router import admin_router as resources_admin_router
from integration_service.resources.router import router as resources_router
from integration_service.resources.service import ResourceService
from integration_service.security import utcnow
from integration_service.snapshots.router import router as snapshots_router
from integration_service.snapshots.service import SnapshotService
from integration_service.workers.router import router as workers_router

logger = logging.getLogger("integration.app")

# Dependency (DB) tạm chết: trả 503 thay vì 500 và KHÔNG làm sập tiến trình.
_DB_DOWN = (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError, TimeoutError, asyncio.TimeoutError)
FAILED_AUTH_PER_MIN = 30   # số lần xác thực API key SAI tối đa mỗi phút mỗi IP

_DOC_MODELS = [schemas.ResourceBatch, schemas.ResourceUpsertBatch, schemas.ContentBatch, schemas.MetricsBatch,
               schemas.EventBatch, schemas.CommitRequest, schemas.SnapshotCreate, HandshakeRequest, ProjectUpsert,
               CredentialCreate, RotateRequest, ClientPatch, job_schemas.JobCreate, job_schemas.JobClaimRequest,
               job_schemas.JobCompleteRequest, job_schemas.JobFailRequest, job_schemas.JobHeartbeatRequest]

DESCRIPTION = (
    "Cổng duy nhất cho Tool/hệ thống ngoài. Ba mặt phẳng xác thực độc lập: **user** (access token RS256 do Identity ký), "
    "**máy** (API key `mpk_<key_id>.<secret>`, scope + platform) và **nội bộ** (`X-Service-Key`, không nằm trong tài liệu "
    "này). Mọi phản hồi dùng envelope `{ok, data, meta}` / `{ok:false, error:{code,message,details}, meta}`; Tool rẽ nhánh theo "
    "`error.code`, KHÔNG parse `message`. Hợp đồng dữ liệu: `docs/api/ingest-data-contract-v1.md` (contract `2026-10-01`).")


def create_app(settings: Settings | None = None, *, database: Database | None = None,
               verifier: TokenVerifier | None = None, now: Callable[[], datetime] = utcnow,
               identity_feed: FeedClient | None = None, clock=None) -> FastAPI:
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

    background: list[asyncio.Task] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owns_db:
            await db.open()  # pool lười (min_size=0): DB chết lúc boot không làm sập tiến trình
        container: Container = app.state.container
        await container.verifier.prefetch()  # warm-up JWKS: token verify ngay khi service sống
        if settings.maintenance_interval_s > 0:
            background.append(asyncio.create_task(maintenance.loop(container), name="integration-maintenance"))
        consumer = app.state.provisioning
        if consumer is not None:
            background.append(asyncio.create_task(consumer.run_forever(), name="integration-provisioning"))
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            background.clear()
            if owns_db:
                await db.close()
            await container.verifier.aclose()

    app = create_service_app(service_name=SERVICE_NAME, service_version=__version__,
                             readiness_checks={"database": database_ready}, lifespan=lifespan,
                             description=DESCRIPTION)

    verifier = verifier or TokenVerifier(issuer=settings.issuer, audience=settings.audience, jwks_url=settings.jwks_url)
    rate_clock = clock or __import__("time").monotonic
    rate_limiter = FixedWindowLimiter(settings.rate_limit_per_min, clock=rate_clock)
    fail_limiter = FixedWindowLimiter(FAILED_AUTH_PER_MIN, clock=rate_clock)
    audit = AuditService(db, now)
    jobs = JobService(db, settings, now)
    redeem_limiter = FixedWindowLimiter(settings.legacy_redeem_per_window, clock=rate_clock,
                                        window=float(settings.legacy_redeem_window_s))
    clients = ClientService(db, settings, now)
    reveal_crypto = RevealCrypto(settings.reveal_key) if settings.reveal_key else None
    credentials = CredentialService(db, settings, now, clients, rate_limiter, fail_limiter,
                                    reveal_crypto=reveal_crypto)
    app.state.container = Container(
        settings=settings, db=db, now=now, verifier=verifier, service_auth=ServiceKeyAuth(dict(settings.feed_keys)),
        audit=audit, clients=clients, credentials=credentials,
        installations=InstallationService(now), projects=ProjectService(db, now), resources=ResourceService(db, now),
        snapshots=SnapshotService(db, settings, now), guide=GuideService(db, settings, clients, now),
        runner=MachineRunner(db, now, audit),
        jobs=jobs, legacy=LegacyService(db, settings, now, jobs, redeem_limiter),
        rate_limiter=rate_limiter, fail_limiter=fail_limiter)

    # Consumer provisioning: thiếu URL/khoá => tắt (service vẫn chạy bình thường).
    app.state.provisioning = None
    if identity_feed is not None or settings.provisioning_enabled:
        feed = identity_feed or FeedClient(settings.identity_feed_url, settings.identity_feed_key)
        app.state.provisioning = ProvisioningConsumer(db, settings, clients, credentials, feed, now)

    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    install_auth_error_handler(app, "envelope")

    @app.exception_handler(ApiProblem)
    async def _api_problem(_: Request, exc: ApiProblem) -> JSONResponse:
        return EnvelopeResponse(status_code=exc.status_code, headers=exc.headers or None,
                                content=error_envelope(exc.code.value, exc.message, request_id=current_request_id(),
                                                       details=exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        if legacy_errors.is_legacy_path(request.url.path):   # route legacy giữ lỗi kiểu `{detail: [...]}`
            return legacy_errors.validation_response(exc)
        # Chỉ vị trí + loại lỗi, KHÔNG phản chiếu giá trị đầu vào. Mặt phẳng máy dùng mã RECORD_VALIDATION_FAILED.
        machine_plane = getattr(request.state, "plane", "user") == "machine"
        fields = [{"loc": [str(p) for p in e.get("loc", ())], "type": e.get("type", "")} for e in exc.errors()[:20]]
        owner = any(f["loc"] and f["loc"][-1].lower().startswith("owner") and f["type"] == "extra_forbidden"
                    for f in fields)
        code = ErrorCode.RECORD_VALIDATION_FAILED if machine_plane else ErrorCode.REQUEST_VALIDATION_FAILED
        if machine_plane and request.url.path.startswith("/v1/integrations/projects/"):
            code = ErrorCode.PROJECT_VALIDATION_FAILED
        message = ("Trường owner_* không được gửi: quyền sở hữu suy ra từ credential" if owner
                   else "Request validation failed")
        return EnvelopeResponse(status_code=422, content=error_envelope(
            code.value, message, request_id=current_request_id(), details={"fields": fields}))

    async def _db_down(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("Database không khả dụng: %s", type(exc).__name__)
        if legacy_errors.is_legacy_path(request.url.path):
            return JSONResponse(status_code=503, content={"detail": "Dịch vụ tạm thời không khả dụng"})
        return EnvelopeResponse(status_code=503, content=error_envelope(
            ErrorCode.UNAVAILABLE.value, "Dịch vụ tạm thời không khả dụng", request_id=current_request_id()))

    for error_type in _DB_DOWN:  # Starlette nhận từng lớp lỗi, không nhận tuple
        app.add_exception_handler(error_type, _db_down)

    @app.exception_handler(asyncpg.DataError)
    async def _bad_data(request: Request, exc: Exception) -> JSONResponse:
        # Đầu vào mà Postgres không chấp nhận (vd ký tự NUL): lỗi của người gọi, không phải lỗi máy chủ.
        logger.info("Dữ liệu không hợp lệ: %s", type(exc).__name__)
        if legacy_errors.is_legacy_path(request.url.path):
            return JSONResponse(status_code=422, content={"detail": "Dữ liệu không hợp lệ"})
        return EnvelopeResponse(status_code=422, content=error_envelope(
            ErrorCode.RECORD_VALIDATION_FAILED.value, "Dữ liệu không hợp lệ", request_id=current_request_id()))

    for router in (clients_router, credentials_router, installations_router, projects_router, resources_router,
                   snapshots_router, platforms_router, audit_router, resources_admin_router, jobs_machine_router,
                   jobs_user_router, workers_router, channel_groups_router, guide_router):
        app.include_router(router)
    legacy_errors.install(app)
    for legacy_router in build_legacy_routers(include_in_schema=False):   # ACL: hợp đồng = OpenAPI legacy đóng băng
        app.include_router(legacy_router)
    app.include_router(build_feed_router(app.state.container.service_auth))
    install_openapi_models(app, _DOC_MODELS)
    return app
