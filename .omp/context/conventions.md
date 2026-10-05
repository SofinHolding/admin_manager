<!-- Nguồn: đọc trực tiếp admin-server/{main.py,auth.py,admin_api.py,viewer.py,cache.py}, reward-service/{main.py,settings.py,security.py,api/*.py}, admin-web/{App.tsx,auth.tsx,api.ts}. Sinh bởi vibe-init 2026-09-28. -->

# conventions.md

## Backend Python (admin-server + reward-service)

- **Router pattern**: mỗi module business (`auth.py`, `viewer.py`, `admin_api.py`, `api/jobs.py`, ...)
  export `make_router(db, ...)` nhận dependency qua tham số (không dùng FastAPI `Depends` global cho DB)
  — `main.py` gọi `make_router()` rồi `app.include_router()`.
- **`_load_dotenv()`**: cả 2 service tự viết loader riêng dùng `setdefault` (biến shell/pm2 LUÔN thắng
  `.env`), gọi TRƯỚC mọi import module đọc secret lúc import-time (`auth.JWT_SECRET`,
  `settings.load_settings()`). Không dùng `python-dotenv`.
- **Config lỗi**: reward-service ném `RewardConfigError` (custom exception), KHÔNG BAO GIỜ `sys.exit()`
  trong tầng settings — để test import được module mà không crash process.
- **2 tầng phân quyền** (reward-service, `security.py`, áp dụng khi thêm route mới):
  - T1 — chỉ verify chữ ký + role trong JWT payload, không chạm DB (`require_reward_user`,
    `require_discord`, `require_admin_role`). Dùng cho route đọc rẻ/tần suất cao.
  - T2 — `require_active_account`: tra lại `accounts.status/role` trong DB, so tier
    (`viewer=0 < discord=1 < admin=2`). **Bắt buộc cho MỌI endpoint GHI** (tránh JWT còn hạn dùng được
    sau khi bị revoke/suspend).
  - admin-server hiện KHÔNG nhất quán: `auth.get_current_user` có tra DB+check suspended,
    `admin_api.require_admin` KHÔNG tra DB (chỉ tin JWT payload `role=="admin"`), `viewer.require_user`
    cũng không tra DB. Route mới trong `admin-server` nên theo mẫu T1/T2 của reward-service nếu ghi dữ
    liệu nhạy cảm.
- **Rate limit**: implement tay bằng dict in-memory theo `(ip, action)` window (`auth._check_rate`),
  KHÔNG dùng thư viện ngoài (`slowapi`...). Áp cho mọi endpoint public dễ bị dò/spam (login, register,
  resend-verify, forgot-password, validate-key).
- **Timing-safe compare**: `hmac.compare_digest` cho mã 6 số đổi mật khẩu; delay 1s cố định khi sai mật
  khẩu login (chống timing attack + brute-force nhẹ).
- **Không tiết lộ tồn tại tài khoản**: `resend-verify`, `forgot-password` luôn trả response giống nhau
  bất kể email có tồn tại hay không.
- **Write-ahead trước side-effect ngoài**: `reward-service` claim item (`UPDATE...FOR UPDATE SKIP
  LOCKED`) + `INSERT attempt(phase='prepared')` TRONG CÙNG 1 transaction, TRƯỚC khi gọi Discord API —
  nguyên tắc bắt buộc khi thêm hành động không idempotent gọi ra ngoài: ghi ý định trước, gọi ngoài sau,
  chốt kết quả sau cùng.
- **Không bao giờ retry thao tác không idempotent bằng cách quay lại trạng thái ban đầu**: item lỗi
  network SAU khi đã gửi lệnh chỉ được vào `retrying`/`unknown`, KHÔNG BAO GIỜ về lại `pending` (tránh
  gửi trùng). Áp dụng khi thiết kế state machine mới.
- **Secret không log/trả API**: token Discord chỉ giải mã ở 2 điểm cố định
  (`api/credentials.py:_verify_and_upsert`, `engine/worker_manager.py:start`, biến cục bộ `del` ngay sau
  dùng); `_public_view`/response schema loại field token tuyệt đối.
- **Migration**: file SQL thuần trong `store/migrations/`, chạy idempotent bằng `CREATE TABLE IF NOT
  EXISTS` qua `store/migrate.py` lúc `lifespan` boot (`REWARD_DB_MIGRATE_ON_BOOT`), KHÔNG dùng
  Alembic/ORM migration tool.

## Cache (admin-server/cache.py)

- Cache-aside, KHÔNG BAO GIỜ throw khi Redis lỗi (fail-open — mất cache, không mất chức năng).
- Key namespace cố định `adm:` + version schema `v1` — tăng `SCHEMA` thủ công khi đổi shape output của
  `stats_core.py` để tự vô hiệu cache cũ, KHÔNG cần flush tay.
- Chỉ cache method ĐỌC thống kê nặng (5 method cố định trong `_CACHED`); mọi method ghi/auth đi thẳng
  qua `__getattr__`, không cache.
- Invalidate dựa vào `doc_version` đổi (tự nhiên theo dữ liệu mới) + TTL, KHÔNG có invalidate tường minh
  khi admin-server tự ghi dữ liệu của nó (vì nó không ghi vào `documents`).

## Frontend (admin-web)

- **Token**: access token (JWT ngắn hạn) CHỈ giữ trong biến JS memory, KHÔNG BAO GIỜ ghi vào
  localStorage/sessionStorage (chống XSS đọc token dài hạn). Refresh token (dài hạn) mới lưu storage,
  chọn `localStorage`/`sessionStorage` theo checkbox "Ghi nhớ".
- **Auto-refresh 401**: mọi call qua `apiFetch`/`apiFetchRaw` tự thử refresh 1 lần khi 401, gộp nhiều
  request refresh đồng thời thành 1 Promise chung (tránh race xoay vòng token).
- **SSE không dùng `EventSource`**: khi cần gắn header `Authorization`, đọc `fetch()` + `ReadableStream`
  thủ công, tự parse khung `event:`/`data:`; fallback polling khi stream lỗi/đóng.
- **RoleGate theo mảng role cho phép**, `role` là `string` tự do phía TypeScript (không enum) — validate
  giá trị hợp lệ nằm ở backend (`accounts.role` CHECK phía admin-server, không CHECK ở DB thật — chỉ
  Pydantic khi ghi qua API).
- **Cấu trúc route**: mỗi feature domain (`reward/`) có `pages/`, `hooks/`, `api.ts` riêng, tách khỏi
  `src/routes/` (feature chính admin/dashboard) — theo module, không theo loại file.

## Quy ước dữ liệu liên repo (admin_manager ↔ upload-facebook)

- `stats_core.py`: sửa công thức PHẢI sửa bản canonical `upload-facebook/app/analytics/stats_core.py`
  trước, copy nguyên byte sang `admin-server/stats_core.py` sau — không sửa trực tiếp bản copy.
- Đổi shape 1 file JSON store bên Tool → kiểm tra `admin-server/db.py` (hàm `_load_*` tương ứng) có cần
  cập nhật field đọc không.
- `DATA_DIR` (admin-server) và `APP_DATA_DIR` (Tool) PHẢI trỏ cùng 1 thư mục trong dev — lệch là mọi số
  liệu ra rỗng/0, dễ tưởng nhầm code hỏng (bài học ghi lại từ chính docs của Tool).
- `JWT_SECRET` PHẢI giống hệt giữa `admin-server/.env` và `reward-service/.env` (copy nguyên văn) —
  `reward-service/scripts/check-env.py` chặn khởi động nếu lệch (so sánh SHA-256 fingerprint).
