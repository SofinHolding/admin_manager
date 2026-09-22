"""reward-service — app factory FastAPI, port 8422 (khung Phase 1).

Tách khỏi `admin_manager` (:8421) nhưng dùng chung Postgres `ufsync` và `JWT_SECRET`. Service này KHÔNG
BAO GIỜ import hay sửa code trong `admin_manager/`; chỉ đọc bảng `accounts` và ghi cột `role`
(`store/repositories/accounts.py`). Toàn bộ schema riêng nằm trong các bảng `reward_*`
(`store/migrations/001_reward_init.sql`).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def _load_dotenv() -> None:
    """Nạp biến từ .env vào os.environ — KHÔNG đè biến đã set sẵn (shell/pm2 thắng).

    Cùng khuôn mẫu `admin_manager/admin-server/main.py:_load_dotenv` — PHẢI chạy TRƯỚC các import cục
    bộ bên dưới (`settings.py` đọc env ngay lúc `create_app()` chạy)."""
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

from settings import load_settings  # noqa: E402
from security import Security  # noqa: E402
from crypto import TokenCrypto  # noqa: E402
from store.pool import Pool  # noqa: E402
from store import migrate  # noqa: E402
from discord.http import DiscordHttpClient  # noqa: E402
from engine import recovery  # noqa: E402
from engine.worker_manager import WorkerManager  # noqa: E402
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
    security = Security(settings, pool)
    http_client = httpx.AsyncClient(base_url=settings.admin_api_base, timeout=10.0)
    discord_http = DiscordHttpClient(read_timeout_s=settings.http_timeout_s)
    crypto = TokenCrypto(settings.reward_token_key)
    worker_manager = WorkerManager(pool=pool, settings=settings, crypto=crypto)

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
        yield
        await worker_manager.shutdown(timeout_s=30.0)
        await discord_http.aclose()
        await http_client.aclose()
        await pool.close()

    app = FastAPI(title="reward-service", version=auth_proxy.VERSION, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_proxy.make_router(settings, security, pool, http_client))
    app.include_router(admin_api.make_router(security, pool))
    app.include_router(credentials_api.make_router(security, pool, settings, discord_http, crypto))
    app.include_router(jobs_api.make_router(security, pool, worker_manager))

    return app

app = create_app()
