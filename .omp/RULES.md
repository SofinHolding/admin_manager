# Rules — Admin Manager

## Đọc context trước khi code

Đọc `.omp/AGENTS.md` + file `context/*.md` liên quan TRƯỚC khi sửa code. Task chạm nhiều service →
đọc `context/modules.md` để biết ranh giới trước khi đụng file.

## Conventions dự án

- **Router pattern**: mỗi module business export `make_router(db, ...)`, nhận dependency qua tham số,
  `main.py` gọi rồi `include_router()` — theo mẫu này khi thêm route mới, KHÔNG dùng global `Depends`
  cho DB connection.
- **`_load_dotenv()` tự viết** (không `python-dotenv`), dùng `setdefault` (env thắng .env), PHẢI gọi
  TRƯỚC import module đọc secret lúc import-time.
- **Rate limit** implement tay bằng dict in-memory theo `(ip, action)` — không thêm thư viện ngoài
  (`slowapi`...) trừ khi có lý do rõ ràng.
- **Cache**: chỉ cache method ĐỌC nặng qua `CachedDb._CACHED`, KHÔNG cache method ghi/auth. Đổi shape
  output `stats_core.py` → PHẢI tăng `SCHEMA` trong `cache.py` để tự vô hiệu cache cũ.
- **reward-service — write-ahead bắt buộc**: mọi hành động gọi Discord (không idempotent) PHẢI ghi ý
  định vào DB (`phase='prepared'`) TRONG CÙNG transaction claim item, TRƯỚC khi gọi ra ngoài.
- **Item/job state machine**: KHÔNG BAO GIỜ đưa item lỗi-sau-khi-gửi về lại `pending` (chống gửi trùng).
  Transition `unknown→success|pending` CHỈ hợp lệ khi `actor='operator'`.
- **Secret Discord token**: chỉ giải mã ở 2 điểm cố định trong `reward-service`
  (`api/credentials.py:_verify_and_upsert`, `engine/worker_manager.py:start`), `del` biến ngay sau
  dùng, KHÔNG BAO GIỜ log/trả API.
- **Frontend**: access token JWT CHỈ giữ trong biến JS memory, KHÔNG BAO GIỜ ghi vào
  localStorage/sessionStorage. SSE cần header Authorization → dùng `fetch`+`ReadableStream` thủ công,
  KHÔNG dùng `EventSource`.
- **Migration SQL thuần** (`store/migrations/*.sql`, idempotent `CREATE IF NOT EXISTS`), KHÔNG dùng
  Alembic/ORM.

## Modules và phạm vi

| Module | Đường dẫn | Ghi chú phạm vi |
|---|---|---|
| admin-server | `admin-server/` | Sở hữu bảng `invite_keys`,`accounts`,`refresh_tokens`; CHỈ ĐỌC `documents`/`revs` (production) — KHÔNG BAO GIỜ ghi/ALTER 2 bảng này |
| admin-web | `admin-web/` | SPA dùng chung cho cả dashboard admin lẫn UI reward (routing theo role) |
| reward-service | `reward-service/` | Tiến trình RIÊNG, sở hữu 12 bảng `reward_*`; CHỈ ĐỌC bảng `accounts` (JOIN/SELECT), CHỈ ĐƯỢC UPDATE cột `role`; **KHÔNG BAO GIỜ sửa code `admin-server/`** khi làm việc trong reward-service (ràng buộc ghi rõ trong `reward-service/DEPLOY.md`) |
| upload-facebook (Tool) | `D:/workspace/upload-facebook/` (repo khác) | KHÔNG sửa trực tiếp trừ khi task yêu cầu tường minh — đây là nguồn dữ liệu, không phải phần triển khai của admin_manager. Đổi `stats_core.py` phải đồng bộ cả 2 phía |

## Ràng buộc kỹ thuật

- **Database**: Postgres `ufsync` qua `asyncpg` (production) hoặc SQLite (dev/test) — KHÔNG thêm ORM,
  pattern hiện tại là raw SQL qua các lớp `Db`/repository thủ công.
- **Auth**: JWT HS256, `JWT_SECRET` PHẢI giống hệt giữa `admin-server` và `reward-service` (copy nguyên
  văn khi deploy) — `reward-service/scripts/check-env.py` chặn khởi động nếu lệch.
- **2 tầng phân quyền reward-service**: T1 (chỉ verify JWT) cho route đọc rẻ; T2 (`require_active_
  account`, tra DB) BẮT BUỘC cho mọi route ghi. admin-server hiện CHƯA nhất quán
  (`admin_api.require_admin` không tra DB) — route mới trong admin-server nên theo mẫu T2 của
  reward-service nếu ghi dữ liệu nhạy cảm, KHÔNG lặp lại thiếu sót cũ.
- **Cổng dev**: đã phát hiện lệch giữa comment `admin-web/src/reward/api.ts` (ghi 8421) và
  `admin-web/vite.config.ts` thực tế (proxy `/v1` → `8423`) — dùng **file cấu hình thực tế
  (`vite.config.ts`)** làm nguồn sự thật, không dùng comment.
- **CORS admin-server**: hiện hardcode 3 origin localhost trong `main.py`, KHÔNG đọc từ env — domain
  production thật [chưa xác định]; khi cấu hình CORS cho domain mới, kiểm tra lại `main.py` có cần
  refactor sang đọc env hay không trước khi hardcode thêm.
- **reward-service `--workers 1` bắt buộc** (`run.sh`) — `WorkerManager` giữ state trong RAM tiến
  trình, KHÔNG được tăng worker để scale; muốn scale phải tách máy + hàng đợi.
- **Không đọc thẳng file JSON của Tool ngoài qua `LocalFileDb`** — mọi field đọc phải qua đúng
  method `_load_*` đã có, tránh phá vỡ giả định shape dữ liệu (xem `data-models.md` mục 3).

## Ràng buộc từ người dùng

*(Trống — sẽ cập nhật khi có ràng buộc mới qua PM Agent.)*

## Giới hạn phạm vi

Task chỉ chạm 1 module → KHÔNG sửa code module khác "tiện thể". Task cần đổi hành vi phía Tool (data
producer) → phải nêu rõ đây là thay đổi liên repo, xác nhận với user trước khi sửa
`D:/workspace/upload-facebook`.

## Orchestrate

Dùng `orchestrate <yêu cầu>` sau khi context đã sẵn sàng — pipeline PM → Architect → Code → QA →
Doc Sync sẽ tự đọc `.omp/context/*` + `.omp/AGENTS.md`/`RULES.md` này.

## Giao tiếp

Phản hồi người dùng bằng tiếng Việt (quy tắc toàn cục OMP); code/log/định danh kỹ thuật giữ nguyên gốc.
