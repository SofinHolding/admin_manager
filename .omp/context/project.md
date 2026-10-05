<!-- Nguồn: README.md, DEPLOY.md, reward-service/DEPLOY.md, admin-server/{main.py,.env.example,requirements.txt}, admin-web/{package.json,vite.config.ts}, reward-service/{main.py,settings.py,.env.example,requirements.txt}, D:/workspace/upload-facebook/.omp/context/{PROJECT.md,ARCHITECTURE.md} (repo nguồn dữ liệu "Tool"). Sinh bởi vibe-init 2026-09-28. -->

# project.md

## Dự án là gì

**Admin Manager** (`github.com/SofinHolding/admin_manager`) là dashboard thống kê + quản trị **viewer**
cho hệ thống **upload-facebook** ("Tool" — repo riêng tại `D:/workspace/upload-facebook`, Electron +
FastAPI chạy trên máy người dùng, tự động đăng bài Facebook/reup YouTube/dịch video/cắt highlight).
Admin Manager **không thực thi** nghiệp vụ của Tool (không Chrome, không ffmpeg, không yt-dlp) — nó chỉ
**đọc dữ liệu Tool đã đồng bộ lên VPS** để dựng dashboard, quản lý tài khoản viewer, và (module thứ 3,
`reward-service/`) vận hành phân phối điểm thưởng qua Discord.

Repo này (`admin/` bên trong workspace của Tool nhưng là **Git repo riêng**, Tool đã `.gitignore` thư
mục `admin/`) gồm **3 service độc lập**, dùng chung 1 Postgres (`ufsync`) và 1 `JWT_SECRET`:

| Thư mục | Vai trò | Stack | Cổng mặc định (VPS) | Cổng dev |
|---|---|---|---|---|
| `admin-server/` | API viewer + xác thực tài khoản + đọc thống kê Tool | Python 3.11+ · FastAPI · asyncpg · Redis (cache tuỳ chọn) | `8421` (pm2 `ufadmin`) | dev thấy trong `vite.config.ts` proxy `/v1` → `8423` (khác 8421 hardcode trong comment — lệch, xem RULES) |
| `admin-web/` | Giao diện Dashboard + quản trị + UI Discord reward (gộp chung SPA) | React 19 · Vite · TypeScript · Tailwind v4 · Radix UI · recharts | phục vụ tĩnh bởi admin-server tại `/admin/` (prod) | `5174` (`npm run dev`) |
| `reward-service/` | Phân phối điểm thưởng qua Discord (job runner, tự gọi slash command bot leveling) | Python 3.11+ · FastAPI · asyncpg · websockets (Discord Gateway) · cryptography (Fernet) | `8422` (pm2 `ufreward`) | proxy dev `/v1/reward` → `8422` |

`admin-web` gọi cả `admin-server` (`/v1/...`) lẫn `reward-service` (`/v1/reward/...`) từ cùng 1 SPA,
điều hướng theo `role` tài khoản (`viewer`/`admin`/`discord`).

## Quan hệ với Tool (nguồn dữ liệu) — QUAN TRỌNG

Admin **không giữ dữ liệu gốc**. Có 2 chế độ đọc (biến `MODE` trong `admin-server/.env`):

- **`dev`**: đọc **thẳng thư mục JSON** của Tool trên máy (`LocalFileDb`, `admin-server/db.py:1104+`),
  qua biến `DATA_DIR` — PHẢI khớp `APP_DATA_DIR` bên `.env` của Tool (mặc định Windows:
  `%APPDATA%\fb-uploader-ui\data`, xem `upload-facebook/.omp/context/PROJECT.md:57`). Tài khoản viewer
  vẫn lưu riêng ở `admin-server/admin-auth.db` (SQLite), tách biệt hoàn toàn khỏi dữ liệu Tool.
- **`production`**: đọc bảng `documents` trong **Postgres `ufsync`** — bảng này do **sync server của
  Tool** (`upload-facebook/server/`, tiến trình VPS riêng, KHÔNG import gì từ `app/` của Tool) ghi vào
  qua `POST /v1/push`. Tài khoản viewer lưu trong chính Postgres đó (3 bảng `invite_keys`, `accounts`,
  `refresh_tokens` — do admin-server sở hữu 100%, sync server không biết tới, xem `data-models.md`).

Công thức thống kê ở `admin-server/stats_core.py` là **bản sao byte-identical** của
`upload-facebook/app/analytics/stats_core.py` (xác nhận bằng `diff -u`, không lệch dòng nào) — module
thuần (chỉ `datetime`+stdlib) để 2 backend luôn ra cùng số liệu từ 2 nguồn khác nhau (dev: JSON local;
production: Postgres). Test canh lệch phía Tool: `tests/test_stats_core_vendored_parity.py`,
`tests/test_stats_parity_tool_vs_admin.py`. **Sửa công thức PHẢI sửa ở bản canonical (repo Tool) rồi
copy lại nguyên byte sang đây.**

`reward-service/` KHÔNG đọc dữ liệu Tool — nó chỉ dùng chung Postgres `ufsync` + `JWT_SECRET` với
admin-server, đọc bảng `accounts` (chỉ đọc) để xác thực role, và sở hữu toàn bộ nhóm bảng `reward_*`
riêng (migration `reward-service/store/migrations/001_reward_init.sql`).

### Điểm dễ nhầm: có 2 khái niệm "admin" khác nhau
- **`admin_manager`** (repo này) — dashboard viewer đọc dữ liệu Tool, KHÔNG liên quan code Tool.
- **`upload-facebook/app/admin/`** (`client.py`, `router.py`) — module RIÊNG bên trong Tool, là UI admin
  của **chính Electron app** gọi tới API admin trên **sync server VPS** (`server/admin.py`, endpoint
  `/v1/admin/overview|users|traffic|channels|transfer|...`) để quản lý license key/user đa-tenant của
  Tool. Hai "admin" này **không dùng chung code, không dùng chung route, không dùng chung DB** — chỉ
  trùng tên.

## Entry Points

- `admin-server/main.py::create_app` (module-level, không phải factory) — chạy bằng
  `python -m uvicorn main:app --host 127.0.0.1 --port 8421`. `_load_dotenv()` nạp `.env` bằng
  `setdefault` (biến shell/pm2 thắng) TRƯỚC khi import `admin_api`/`auth`/`email_service`/`viewer`/`db`/
  `cache` (bắt buộc thứ tự vì `auth.JWT_SECRET` đọc env lúc import).
- `reward-service/main.py::create_app` (dòng 63-104) — `python -m uvicorn main:app --port 8422`,
  `run.sh` cố định `--workers 1` (WorkerManager giữ state job trong RAM tiến trình).
- `admin-web/src/main.tsx` — React root, `basename="/admin"`; `src/App.tsx` khai toàn bộ route.
- Swagger: admin-server và reward-service đều là FastAPI mặc định → `/docs` trên từng cổng.

## Commands (dev)

```powershell
# admin-server
cd admin-server
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # sửa DATA_DIR + JWT_SECRET
python -c "import secrets; print(secrets.token_hex(32))"   # sinh JWT_SECRET
$env:DATABASE_URL="sqlite:///./admin-auth.db"; python create_admin.py   # tạo admin đầu tiên
python -m uvicorn main:app --host 127.0.0.1 --port 8421 --reload

# admin-web
cd admin-web
npm install
npm run dev        # http://localhost:5174/admin/

# reward-service
cd reward-service
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # JWT_SECRET PHẢI giống hệt admin-server, REWARD_TOKEN_KEY sinh bằng Fernet
python -m pytest tests/ -q
python -m uvicorn main:app --port 8422 --reload
```

## Repo Layout

```
admin-server/        API viewer + auth (FastAPI, Python)
  main.py            App factory-style, CORS, lifespan, mount router, static SPA fallback /admin/*
  auth.py            /v1/auth/* — register/login/refresh/verify-email/reset-password/profile
  viewer.py          /v1/viewer/* — đọc thống kê (summary/traffic/top-channels/...)
  admin_api.py       /v1/admin/* — invite keys, quản lý user, flush cache
  db.py              SqliteDb / PostgresDb / LocalFileDb — 2 chế độ MODE, DDL 3 bảng sở hữu
  stats_core.py      BẢN SAO byte-identical app/analytics/stats_core.py (Tool)
  cache.py           Cache + CachedDb (Redis, cache-aside, single-flight, version-based invalidation)
  email_service.py   SMTP verify-email + reset-password
  create_admin.py    Bootstrap admin đầu tiên (CLI)
  import_local_data.py  Import data/*.json → SQLite documents (giả lập sync, chỉ dùng test)
admin-web/           SPA React 19 (Vite + Tailwind v4 + Radix + recharts)
  src/routes/        Login/Register/VerifyEmail/ResetPassword/Dashboard/Admin/Profile
  src/reward/        UI module reward (Credentials/Jobs/JobDetail/JobNew) — gọi reward-service
  src/api.ts          client admin-server; src/reward/api.ts client reward-service
  src/auth.tsx        AuthProvider — access token in-memory, refresh token localStorage/sessionStorage
reward-service/      Backend phân phối điểm Discord (FastAPI, Python, tiến trình riêng)
  main.py, settings.py, security.py, crypto.py
  api/               auth_proxy (proxy sang admin-server) · admin (grant/revoke role discord) ·
                     credentials (config Discord/account) · jobs (CRUD + run/pause/stop + SSE stream)
  domain/            error_classifier, idempotency, item_state (state machine), parser
  engine/            confirmation, executor_slash, job_runner, recovery, worker_manager
  discord/           gateway_session (WS), http (REST + retry 429), slash_commands, channel_messages
  store/             pool, migrate, migrations/001_reward_init.sql + 002_reward_tokens.sql, repositories/*.py (12 bảng reward_*)
scripts/setup-vps.sh  Setup VPS lần đầu (build web, tạo thư mục, rsync, venv, nginx, pm2)
.github/workflows/    deploy.yml (admin-web+admin-server), deploy-reward.yml (reward-service, path filter)
```

## Ràng buộc triển khai (VPS)

```
nginx (443) ──/admin/────▶ tĩnh admin-web/dist + pm2 ufadmin :8421 (admin-server phục vụ SPA)
            ──/v1/────────▶ pm2 ufadmin  :8421
            ──/v1/reward/─▶ pm2 ufreward :8422   (PHẢI đứng TRƯỚC /v1/ trong nginx config)
                                    │
                              Postgres ufsync (dùng chung)
                    accounts (đọc + admin-server ghi, reward-service chỉ UPDATE role)
                    reward_* (reward-service sở hữu riêng)
                    documents/user_meta/revs (Tool/server ghi, admin-server CHỈ ĐỌC)
```

CI/CD: push `main` đụng `admin-web/**`|`admin-server/**` → `deploy.yml`; đụng `reward-service/**` →
`deploy-reward.yml` (chạy `pytest` trước, fail thì không deploy; sau đó `scripts/check-env.py` preflight
JWT_SECRET khớp admin trước khi restart pm2).

## Current Status

- Đang vận hành: 3 service tách tiến trình, cùng Postgres `ufsync` trên VPS `tuncon.duckdns.org`.
- `reward-service` chạy **`--workers 1` bắt buộc** — không được scale ngang bằng tăng worker (WorkerManager
  + lock `reward_job_lock` giữ state trong RAM tiến trình).
- [chưa xác định] domain CORS production thật của admin-server (main.py chỉ hardcode 3 origin
  localhost, không đọc env cho CORS — xem RULES.md mục ràng buộc kỹ thuật).
