# Admin Manager

Bảng điều khiển **thống kê & quản trị viewer** cho hệ thống *upload-facebook*. Đây là service
triển khai **độc lập** — không chạy phần thực thi của Tool (không Chrome/ffmpeg/yt-dlp), chỉ **đọc dữ
liệu đã đồng bộ** rồi dựng Dashboard cho người xem (viewer).

Repo này gồm 2 thành phần:

| Thư mục | Vai trò | Stack | Cổng mặc định |
|---|---|---|---|
| [`admin-server/`](admin-server/) | API viewer + xác thực tài khoản | Python 3.11+ · FastAPI | `8421` |
| [`admin-web/`](admin-web/) | Giao diện Dashboard | React 19 · Vite · Tailwind | `5174` (dev) |

`admin-web` gọi `admin-server` qua HTTP; đăng nhập tại `POST /v1/auth/login`.

## Nguồn dữ liệu (quan hệ với Tool)

Admin **không** giữ dữ liệu gốc — nó đọc kho dữ liệu do Tool đồng bộ:

- **DEV** (`MODE=dev`): đọc **thẳng thư mục JSON** của Tool trên máy (`LocalFileDb`), qua biến
  `DATA_DIR`. Tài khoản đăng nhập lưu ở `admin-server/admin-auth.db` (SQLite).
- **PRODUCTION** (`MODE=production`): đọc bảng `documents` trong **Postgres** — bảng này do *sync
  server* của Tool ghi vào. Tài khoản lưu trong chính Postgres đó.

Công thức thống kê nằm ở `admin-server/stats_core.py` — **bản sao byte-identical** của
`app/analytics/stats_core.py` bên repo Tool, để Tool và Admin luôn ra **cùng số liệu**. Sửa công
thức phải sửa ở bản canonical (repo Tool) rồi copy lại; repo Tool có test canh lệch.

## Chạy DEV (Windows / máy có sẵn kho dữ liệu Tool)

### 1. admin-server (API)

```powershell
cd admin-server
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# tạo cấu hình từ mẫu
copy .env.example .env
# → sửa .env: DATA_DIR trỏ đúng kho dữ liệu Tool; đặt JWT_SECRET ngẫu nhiên:
python -c "import secrets; print(secrets.token_hex(32))"

# tạo tài khoản admin đầu tiên (ghi vào admin-auth.db mà LocalFileDb dùng để xác thực)
$env:DATABASE_URL="sqlite:///./admin-auth.db"; python create_admin.py

# chạy
python -m uvicorn main:app --host 127.0.0.1 --port 8421 --reload
```

### 2. admin-web (giao diện)

```powershell
cd admin-web
npm install
npm run dev        # http://localhost:5174/admin/
```

Đăng nhập bằng tài khoản vừa tạo → Dashboard.

## Triển khai PRODUCTION (VPS)

1. Cài Python deps: `pip install -r admin-server/requirements.txt`.
2. Cấu hình `admin-server/.env`:
   ```env
   MODE=production
   DATABASE_URL=postgresql://ufsync:MAT_KHAU@127.0.0.1:5432/ufsync
   JWT_SECRET=<chuỗi hex 64 ký tự, GIỮ BÍ MẬT>
   ADMIN_PORT=8421
   ADMIN_WEB_DIR=/duong/dan/admin-web/dist   # nếu để admin-server phục vụ luôn web
   ```
3. Tạo tài khoản admin trên Postgres: `python create_admin.py` (đọc `DATABASE_URL`).
4. Build web: `cd admin-web && npm ci && npm run build` → `admin-web/dist`.
5. Chạy nền bằng pm2:
   ```bash
   cd admin-server
   pm2 start run.sh --name ufadmin
   pm2 save
   ```
   Đặt sau reverse proxy (Caddy/nginx) để có TLS.

## Cấu hình (`admin-server/.env`)

| Biến | Ý nghĩa |
|---|---|
| `MODE` | `dev` (đọc folder JSON) \| `production` (đọc Postgres) |
| `DATA_DIR` | (dev) thư mục data của Tool — phải khớp `APP_DATA_DIR` bên Tool |
| `DATABASE_URL` | `file:///./admin-auth.db` (dev LocalFileDb) \| `postgresql://…` (prod) |
| `JWT_SECRET` | Khoá ký JWT (≥ 64 hex). Đổi là mọi phiên đăng nhập hết hiệu lực |
| `ADMIN_PORT` | Cổng API (mặc định `8421`) |
| `ADMIN_WEB_DIR` | (tuỳ chọn) thư mục `dist` để admin-server phục vụ luôn frontend |

## Bảo mật

- **Không commit** `.env`, `*.db` (`admin-auth.db` chứa hash mật khẩu bcrypt), `node_modules/`,
  `dist/` — đã có trong `.gitignore`.
- `JWT_SECRET` và mật khẩu Postgres chỉ đặt trong `.env` trên máy chạy, không đưa lên Git.
- Tài khoản luôn tạo qua `create_admin.py` khi deploy, không kèm sẵn trong repo.

## Phát triển cùng repo Tool

Thư mục này được đặt tại `admin/` bên trong workspace của repo Tool (`upload-facebook`) để tiện
sửa song song, nhưng là **repo Git riêng** (remote: `github.com/SofinHolding/admin_manager`). Repo
Tool đã `.gitignore` thư mục `admin/` nên hai repo không lẫn vào nhau.
