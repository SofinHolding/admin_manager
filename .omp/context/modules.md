<!-- Nguồn: scout report ScoutAdminServer/ScoutRewardService/ScoutAdminWeb (đọc trực tiếp source), 2026-09-28. -->

# modules.md

## admin-server/ (FastAPI · Python)

| File | Vai trò 1 dòng |
|---|---|
| `main.py` | App setup: dotenv, CORS (3 origin localhost hardcode), lifespan (setup/close DB+cache), mount router, `/v1/health`, SPA static fallback `/admin/*` |
| `auth.py` | Toàn bộ `/v1/auth/*`: validate-key, check-username/email, register, verify-email, resend-verify, login, refresh, logout, me, forgot/reset-password, change-password (2 bước), update-profile |
| `viewer.py` | `/v1/viewer/*`: summary, traffic, top-channels, views-by-country, country-tags, metrics — decode JWT + resolve phạm vi dữ liệu (`user_ids`/`channel_refs`) theo role (`admin`/`dash_admin` xuyên-user mặc định, role khác chỉ thấy Tool đã liên kết qua `account_links`) |
| `links.py` | `/v1/viewer/links/*`: tạo/liệt kê/gỡ mã liên kết account⇄Tool (`account_links`,`link_codes`); `POST .../redeem` public (rate-limit) cho Tool gọi khi đổi mã lấy liên kết |
| `groups.py` | `/v1/viewer/groups/*`: CRUD group/tag kênh do viewer tự quản lý (`channel_groups`,`channel_group_members`) — KHÔNG phải `repost_groups` của Tool |
| `admin_api.py` | `/v1/admin/*`: invite keys CRUD, list/update/suspend/delete user, flush cache — check role `admin` HOẶC `dash_admin` qua JWT payload (KHÔNG tra DB) |
| `db.py` | 3 backend cùng interface: `SqliteDb`/`PostgresDb` (documents table, production) và `LocalFileDb(SqliteDb)` (đọc JSON local, dev); DDL 7 bảng sở hữu (`invite_keys`,`accounts`,`refresh_tokens`,`account_links`,`link_codes`,`channel_groups`,`channel_group_members`); 5 method viewer nhận `user_ids`/`channel_refs` để lọc phạm vi |
| `stats_core.py` | Bản sao byte-identical `upload-facebook/app/analytics/stats_core.py` — hàm tính KPI thuần (không import gì trong `app/`) |
| `cache.py` | `Cache` (Redis wrapper, fail-open) + `CachedDb` (cache-aside 5 method thống kê, single-flight, version-based key `adm:v2:<doc_version>:<method>:<hash>`, key gồm cả `user_ids`/`channel_refs`) |
| `email_service.py` | Gửi email verify + reset/change-password qua SMTP thuần; fallback log console nếu thiếu cấu hình SMTP |
| `create_admin.py` | CLI bootstrap tài khoản admin đầu tiên (sqlite hoặc postgresql) |
| `import_local_data.py` | Import `data/*.json` → SQLite `documents` giả lập sync — chỉ dùng test, không nằm trong luồng chạy thật |

## admin-web/ (React 19 + Vite + TS)

| File/nhóm | Vai trò 1 dòng |
|---|---|
| `src/App.tsx` | Khai toàn bộ route + bọc ProtectedRoute/RoleGate theo path |
| `src/main.tsx` | React root, router `basename="/admin"` |
| `src/auth.tsx` | AuthProvider: access token in-memory, refresh token localStorage/sessionStorage, auto-refresh 401, đa tab qua `storage` event, cảnh báo/logout khi inactivity 60 phút |
| `src/api.ts` | Client gọi admin-server (`/v1/auth/*`, `/v1/viewer/*`, `/v1/admin/*`), `apiFetch` tự refresh khi 401 |
| `src/reward/api.ts` | Client gọi reward-service (`/v1/reward/*`) |
| `src/reward/hooks/useJobStream.ts` | SSE thủ công (fetch + ReadableStream, không dùng `EventSource` vì cần header Authorization) → fallback polling 2s khi lỗi |
| `src/routes/*.tsx` | Login, Register (3 bước), VerifyEmail, ResetPassword, Dashboard, Admin (`/manage`), Profile |
| `src/reward/pages/*.tsx` | CredentialsPage, JobsPage, JobNewPage, JobDetailPage (theo dõi realtime) |
| `src/components/{ProtectedRoute,RoleGate,SessionExpired,SessionWarning,Layout}.tsx` | Route guard theo role, overlay hết phiên, cảnh báo timeout, layout khung |

## reward-service/ (FastAPI · Python, tiến trình riêng)

| File/nhóm | Vai trò 1 dòng |
|---|---|
| `main.py` | App riêng cổng 8422; lifespan: mở pool → migrate → recovery orphaned attempts → shutdown WorkerManager |
| `settings.py` | Đọc toàn bộ env, ném `RewardConfigError` (không `sys.exit`) nếu thiếu bắt buộc |
| `security.py` | 2 tầng phân quyền: T1 (`require_reward_user`/`require_discord`/`require_admin_role` — chỉ verify JWT) và T2 (`require_active_account` — tra DB `accounts`, so tier role) |
| `crypto.py` | `TokenCrypto` (Fernet) mã hoá/giải mã token Discord bằng `REWARD_TOKEN_KEY` |
| `api/auth_proxy.py` | Proxy login/refresh/logout nguyên trạng sang admin-server; `me`/`validate`/`health` verify JWT local |
| `api/admin.py` | Quản trị: list accounts, grant/revoke role `discord`, role-audit, list jobs/locks toàn hệ thống, force-release lock |
| `api/credentials.py` | CRUD cấu hình Discord của account (guild/channel/command/pattern...), verify token, KHÔNG BAO GIỜ trả token |
| `api/jobs.py` | CRUD job + validate/run/pause/resume/stop/resolve-item + export CSV/XLSX + SSE stream |
| `domain/item_state.py` | State machine item (`pending→processing→success/failed/unknown/retrying`), `assert_transition` |
| `domain/error_classifier.py` | Phân loại lỗi HTTP/network Discord → `retry`/`fail`/`pause_job` |
| `domain/idempotency.py` | `item_key` (sha256 job+row+username+point), `next_nonce` |
| `domain/parser.py` | Parse input job (danh sách username|point) |
| `engine/job_runner.py` | Vòng lặp chính: claim item → executor → confirmation → chốt kết quả, heartbeat lock |
| `engine/executor_slash.py` | Resolve Discord user + gọi slash command bot leveling qua REST |
| `engine/confirmation.py` | Poll message kênh, khớp `success_pattern`/`failure_pattern` để xác nhận kết quả |
| `engine/worker_manager.py` | Giữ state job đang chạy trong RAM tiến trình, giới hạn `max_concurrent_jobs`, chiếm/giải phóng lock |
| `engine/recovery.py` | Chạy lúc lifespan: phục hồi attempt/item kẹt sau crash, KHÔNG BAO GIỜ trả item về `pending` |
| `discord/gateway_session.py` | Kết nối Discord Gateway (WS) bằng user token — lấy session_id, resolve member |
| `discord/http.py` | REST client dùng chung, retry 429 theo `retry_after` |
| `discord/slash_commands.py` | `fetch_guild_command`, `invoke_slash_command` (POST `/interactions`) |
| `discord/channel_messages.py` | `fetch_channel_messages`; có `post_channel_message` không thấy call site (nghi legacy/dự phòng) |
| `store/repositories/*.py` | 1 file / 1-2 bảng `reward_*`, CRUD tương ứng (xem data-models.md) |
| `store/migrate.py` | Chạy migration idempotent (`CREATE IF NOT EXISTS`) lúc boot |

## upload-facebook (Tool — repo nguồn dữ liệu, KHÔNG thuộc admin_manager)

| Thư mục | Vai trò 1 dòng |
|---|---|
| `app/analytics/` | Bản canonical `stats_core.py` mà admin-server copy nguyên byte |
| `app/sync/` | Client đồng bộ VPS: `registry.py` (whitelist store được phép sync), `client.py`, `worker.py`, `applier.py` |
| `server/` | Sync server VPS (KHÔNG import `app/`) — ghi bảng `documents`/`revs`/`user_meta`, expose `/v1/session`,`/v1/push`,`/v1/pull` |
| `app/admin/` | UI admin RIÊNG của Electron gọi tới `server/admin.py` (VPS) — quản lý license/user đa-tenant Tool, KHÔNG liên quan admin_manager |
| `app/accounts/`, `app/platforms/`, `app/repost/`, `app/shorts/`, `app/translate/`, `app/highlights/` | Nghiệp vụ đăng bài/reup/dịch/cắt highlight — admin_manager không đọc trực tiếp các module này, chỉ đọc JSON/documents chúng ghi ra |
