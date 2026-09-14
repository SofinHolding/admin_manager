"""Admin web server — process riêng, port 8421.

Mount 2 router (auth + viewer) và phục vụ file tĩnh frontend (React build).
KHÔNG import gì từ `server/` — hai process độc lập, crash một bên không ảnh hưởng bên kia.

Chế độ chạy (đặt trong .env):

  MODE=dev
    • DATABASE_URL=file:///./admin-auth.db  (SQLite cho auth)
    • DATA_DIR=../data  (thư mục data/ của Electron — đọc JSON trực tiếp, LUÔN mới nhất)
    • Admin web đọc thẳng file JSON — không cần import, không cần Postgres
    • Bất kỳ thay đổi nào trong Electron (thêm pairing, đăng bài xong, cào view mới)
      lập tức hiển thị khi reload trang admin

  MODE=production
    • DATABASE_URL=postgresql://ufsync:password@127.0.0.1:5432/ufsync
    • Admin web đọc từ Postgres VPS — cùng DB mà Electron sync dữ liệu lên

Chạy dev:
    MODE=dev DATA_DIR=../data DATABASE_URL=file:///./admin-auth.db \\
    python -m uvicorn main:app --host 127.0.0.1 --port 8421 --reload

Chạy production (VPS):
    # Source .env rồi chạy
    source .env && python -m uvicorn main:app --host 0.0.0.0 --port 8421
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

def _load_dotenv() -> None:
    """Nạp biến từ .env vào os.environ — KHÔNG đè biến đã set sẵn (shell/pm2 thắng).

    Thứ tự tìm (cái trước thắng): .env CÙNG THƯ MỤC service (production trên VPS đặt ở đây),
    rồi .env ở REPO ROOT (dev dùng chung). Không có file nào → dùng env thật.

    PHẢI chạy TRƯỚC các import cục bộ bên dưới — `auth.py` đọc JWT_SECRET NGAY lúc import."""
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

# Import CỤC BỘ đọc env NGAY lúc import (vd auth.JWT_SECRET) → PHẢI đặt sau _load_dotenv().
import admin_api  # noqa: E402
import auth  # noqa: E402
import email_service  # noqa: E402
import viewer  # noqa: E402
from db import make_db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("admin")

# ── Cấu hình từ biến môi trường ─────────────────────────────────────────────────
MODE = os.environ.get("MODE", "production").lower()
DATA_DIR = os.environ.get("DATA_DIR", "../data")
ADMIN_WEB_DIR = os.environ.get("ADMIN_WEB_DIR", "")

# DATABASE_URL mặc định theo mode
if MODE == "dev":
    _default_db_url = "file:///./admin-auth.db"
else:
    _default_db_url = "sqlite:///./admin.db"  # fallback khi production chưa cấu hình
DATABASE_URL = os.environ.get("DATABASE_URL", _default_db_url)

_db = make_db(DATABASE_URL, data_dir=DATA_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _db.setup()
    if MODE == "dev":
        logger.info("Admin server khoi dong — MODE=dev, doc data tu: %s",
                    os.path.abspath(DATA_DIR))
    else:
        logger.info("Admin server khoi dong — MODE=production, DB: %s",
                    "postgres" if "postgresql" in DATABASE_URL or "postgres://" in DATABASE_URL
                    else "sqlite")
    yield
    await _db.close()


app = FastAPI(title="Admin web server", lifespan=lifespan)

# CORS — cho phép frontend dev server (localhost:5173) gọi API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth.make_router(
    _db,
    send_verify_email=email_service.send_verify_email,
    send_reset_email=email_service.send_reset_email,
))
app.include_router(viewer.make_router(_db, decode_token=auth._decode_access_token))
app.include_router(admin_api.make_router(_db, decode_token=auth._decode_access_token))


@app.get("/v1/health", include_in_schema=False)
async def health() -> dict:
    return {"ok": True, "service": "admin"}


# File tinh frontend — mount SAU routers de API khong bi StaticFiles chan
# StaticFiles can phai co thu muc ton tai; khi chua build frontend thi bo qua
if ADMIN_WEB_DIR:
    _web_dir = Path(ADMIN_WEB_DIR)
    if _web_dir.is_dir():
        # SPA fallback: moi route khong khop API deu tra index.html de React Router xu ly
        app.mount("/admin", StaticFiles(directory=str(_web_dir), html=True), name="admin-web")
        logger.info("Mount frontend tai /admin tu %s", _web_dir)
    else:
        logger.warning("ADMIN_WEB_DIR=%s khong ton tai — chi chay API", ADMIN_WEB_DIR)
