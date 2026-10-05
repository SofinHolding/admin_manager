"""reward-service — app factory FastAPI, port 8422.

Phase 4: Reward chạy trên DB RIÊNG `reward_db` (user `reward_svc`), KHÔNG đọc/ghi bảng của Identity và KHÔNG dùng
JWT secret chung. Access token RS256 do Identity ký, verify OFFLINE bằng JWKS (`manager_sdk.TokenVerifier`).
Quyền (`reward:access`, `reward:admin`) enforce cục bộ từ claim `perms`; thay đổi quyền/phiên đến qua feed nội bộ
của Identity (`identity_link/consumer.py`) → denylist cục bộ + tạm dừng job. Gọi Identity chỉ ở các route proxy
auth và grant/revoke (API công khai, có timeout/backoff).
Toàn bộ schema riêng nằm trong các bảng `reward_*` và bảng sổ sách consumer (`store/migrations/`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def _load_dotenv() -> None:
    """Nạp biến từ .env vào os.environ — KHÔNG đè biến đã set sẵn (shell/pm2 thắng).

    PHẢI chạy TRƯỚC các import cục bộ bên dưới (`settings.py` đọc env ngay lúc `create_app()` chạy)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, ".env"), os.path.join(here, "..", ".env")):
        if not os.path.isfile(cand):
            continue
        try:
            with open(cand, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass


_load_dotenv()

import sdk_path  # noqa: E402,F401  — đưa manager_sdk vào sys.path trước khi import
from manager_sdk import FeedClient, TokenVerifier  # noqa: E402

from settings import load_settings  # noqa: E402
from security import Security  # noqa: E402
from crypto import TokenCrypto  # noqa: E402
from store.pool import Pool  # noqa: E402
from store import migrate  # noqa: E402
from discord.http import DiscordHttpClient  # noqa: E402
from engine import recovery  # noqa: E402
from engine.worker_manager import WorkerManager  # noqa: E402
from identity_link.client import IdentityClient, IdentityUnavailable  # noqa: E402
from identity_link.consumer import FeedConsumer  # noqa: E402
from api import admin as admin_api  # noqa: E402
from api import auth_proxy  # noqa: E402
from api import credentials as credentials_api  # noqa: E402
from api import jobs as jobs_api  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("reward.main")


def create_app() -> FastAPI:
    """Đọc settings (ném `RewardConfigError` nếu sai — KHÔNG `sys.exit`) rồi dựng app FastAPI."""
    settings = load_settings()
    pool = Pool(settings.database_url)
    verifier = TokenVerifier(
        issuer=settings.issuer, audience=settings.audience, jwks_url=settings.identity_jwks_url,
        timeout=settings.identity_timeout_s)
    security = Security(pool, verifier)
    identity = IdentityClient(
        settings.identity_url, timeout=settings.identity_timeout_s, retries=settings.identity_retries,
        backoff_base=settings.identity_backoff_base_s)
    discord_http = DiscordHttpClient(read_timeout_s=settings.http_timeout_s)
    crypto = TokenCrypto(settings.reward_token_key)
    worker_manager = WorkerManager(pool=pool, settings=settings, crypto=crypto)

    feed_client: FeedClient | None = None
    consumer: FeedConsumer | None = None
    if settings.feed_enabled:
        feed_client = FeedClient(
            settings.identity_feed_url, settings.identity_feed_key, timeout=settings.identity_timeout_s,
            retries=1, backoff_base=settings.identity_backoff_base_s)
        consumer = FeedConsumer(
            pool=pool, feed=feed_client, on_pause=worker_manager.pause_account_jobs,
            batch_size=settings.feed_batch_size, max_attempts=settings.feed_event_max_attempts,
            backoff_base=settings.feed_event_backoff_base_s, poll_interval=settings.feed_poll_interval_s)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await pool.open()
        if settings.db_migrate_on_boot:
            version = await migrate.run(pool)
            logger.info("Migration hiện tại: %s", version)
        else:
            logger.info("REWARD_DB_MIGRATE_ON_BOOT=0 — bỏ qua migrate lúc khởi động")
        # Phục hồi sau crash TRƯỚC khi nhận request đầu tiên (D.5/D.2 recovery.py) — idempotent.
        await recovery.recover_orphaned_attempts(pool)
        consumer_task: asyncio.Task | None = None
        if consumer is not None:
            consumer_task = asyncio.create_task(consumer.run(), name="reward-identity-feed")
        else:
            logger.warning("REWARD_IDENTITY_FEED_KEY chưa cấu hình — consumer feed TẮT "
                           "(thu quyền/khoá tài khoản chỉ có hiệu lực khi access token hết hạn)")
        yield
        if consumer is not None and consumer_task is not None:
            consumer.stop()
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)
        await worker_manager.shutdown(timeout_s=30.0)
        await discord_http.aclose()
        await identity.aclose()
        await verifier.aclose()
        if feed_client is not None:
            await feed_client.aclose()
        await pool.close()

    app = FastAPI(title="reward-service", version=auth_proxy.VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.verifier = verifier
    app.state.identity = identity
    app.state.consumer = consumer
    app.state.worker_manager = worker_manager
    app.state.pool = pool

    @app.exception_handler(IdentityUnavailable)
    async def _identity_unavailable(_request: Request, _exc: IdentityUnavailable) -> JSONResponse:
        message = "Dịch vụ danh tính (Identity) không sẵn sàng, vui lòng thử lại sau"
        return JSONResponse(
            status_code=503, headers={"Retry-After": "5"},
            content={"detail": message, "code": "UNAVAILABLE",
                     "error": {"code": "UNAVAILABLE", "message": message, "details": None}})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_proxy.make_router(settings, security, pool, identity, consumer))
    app.include_router(admin_api.make_router(security, pool, identity, worker_manager))
    app.include_router(credentials_api.make_router(security, pool, settings, discord_http, crypto))
    app.include_router(jobs_api.make_router(security, pool, worker_manager))

    return app


app = create_app()
