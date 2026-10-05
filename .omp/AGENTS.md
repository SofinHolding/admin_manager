# Vibe Flow — Admin Manager

Dashboard thống kê + quản trị **viewer** cho hệ thống **upload-facebook** ("Tool", repo riêng tại
`D:/workspace/upload-facebook`). Admin Manager KHÔNG chạy nghiệp vụ của Tool — chỉ đọc dữ liệu Tool đã
đồng bộ lên VPS (Postgres) hoặc JSON local (dev), dựng dashboard, quản lý tài khoản viewer, và vận hành
module phân phối điểm thưởng Discord (`reward-service/`).

## Stack

| Service | Stack |
|---|---|
| `admin-server/` | Python 3.11+ · FastAPI · asyncpg · bcrypt · PyJWT · Redis (cache tuỳ chọn) |
| `admin-web/` | React 19 · TypeScript · Vite · Tailwind v4 · Radix UI · recharts |
| `reward-service/` | Python 3.11+ · FastAPI · asyncpg · websockets (Discord Gateway) · cryptography (Fernet) |

3 service tách tiến trình, dùng chung Postgres `ufsync` + `JWT_SECRET`. Xem `context/project.md` cho
sơ đồ triển khai VPS đầy đủ.

## Pipeline

PM → Architect → Code → QA → Doc Sync

## Modules hiện tại

- `admin-server/`: auth (register/login/refresh/verify-email/reset-password), viewer (đọc thống kê),
  admin (invite key + quản lý user), cache (Redis cache-aside).
- `admin-web/`: routes chính (Login/Register/Dashboard/Admin/Profile) + module `reward/` (Credentials/
  Jobs/JobDetail/JobNew).
- `reward-service/`: api (auth_proxy/admin/credentials/jobs), domain (state machine + idempotency),
  engine (job_runner/executor_slash/confirmation/worker_manager/recovery), discord (gateway/http/
  slash_commands), store (12 bảng `reward_*` + repositories). Job xoay vòng nhiều token Discord / account (`engine/token_pool.py`).

Chi tiết đầy đủ từng file → `context/modules.md`.

## Flows đã có

- `context/flows/login-auth.md` — đăng nhập/refresh/logout, cả admin-server lẫn proxy reward-service.
- `context/flows/dashboard-stats.md` — đọc thống kê viewer, rẽ nhánh dev/production, cache Redis.
- `context/flows/reward-job-lifecycle.md` — vòng đời reward job đầy đủ (tạo → chạy → xác nhận → phân phối → pause/stop → recovery).
- `context/flows/discord-command-flow.md` — cơ chế gọi slash command Discord + xác nhận kết quả.

## Dữ liệu dự án

| File | Nội dung |
|---|---|
| `context/project.md` | Stack, mục đích, kiến trúc 3 service, quan hệ với Tool, triển khai VPS |
| `context/modules.md` | Danh sách module từng service, 1 dòng/file |
| `context/data-models.md` | Schema đầy đủ: bảng admin-server sở hữu, bảng đọc-only từ Tool (`documents`/`revs`), JSON store dev mode, 12 bảng `reward_*` |
| `context/conventions.md` | Quy ước code Python/React, cache, phân quyền 2 tầng, quy ước liên repo với Tool |
| `context/changelog.md` | Lịch sử thay đổi context |
| `context/flows/*.md` | Trace luồng nghiệp vụ chính, file:hàm cụ thể |

## Quan hệ với repo `upload-facebook` (Tool) — đọc trước khi sửa data layer

Admin Manager phụ thuộc dữ liệu Tool nhưng là **2 repo Git hoàn toàn tách biệt** (Tool đã `.gitignore`
thư mục `admin/`). Khi đổi bất kỳ thứ gì liên quan số liệu/schema, PHẢI đối chiếu:

- `upload-facebook/app/analytics/stats_core.py` ↔ `admin-server/stats_core.py` — PHẢI byte-identical.
- `upload-facebook/app/sync/registry.py` (whitelist store) ↔ `admin-server/db.py` (`_load_*`) — field
  JSON đọc phải khớp field Tool thực sự ghi.
- `upload-facebook/.omp/context/{PROJECT.md,ARCHITECTURE.md,INTEGRATIONS.md,SECURITY.md}` — nguồn sự
  thật về `documents`/`revs`/sync server, KHÔNG suy đoán schema từ phía admin_manager.

## Quy trình đọc context

1. Đọc `AGENTS.md` (file này) + `RULES.md` trước khi bắt đầu bất kỳ task nào.
2. Task chạm data layer/thống kê → đọc thêm `context/data-models.md` + phần liên quan trong
   `upload-facebook/.omp/context/` (đối chiếu, không sửa bên đó).
3. Task chạm 1 flow nghiệp vụ cụ thể → đọc đúng file `context/flows/<tên>.md` tương ứng, không đọc dàn
   trải.
4. Không tìm thấy context phù hợp → đọc trực tiếp source, cập nhật lại context sau khi xong (Doc Sync).
