# 1. Tổng quan hệ thống

## 1.1 Mục đích

**Manager Platform** là hệ thống quản lý/giám sát nội dung đa nền tảng (Facebook, YouTube, và bất kỳ nền tảng nào Tool ngoài đẩy lên). Người dùng đăng nhập vào
web quản trị (**manager-web** = `admin-web/`), xem Dashboard (KPI, traffic, kênh, quốc gia...), quản lý resource/job. Các **Tool** bên ngoài
(công cụ thu thập dữ liệu) đẩy dữ liệu vào Manager qua một **API tích hợp thống nhất** bằng machine credential.

Hệ cũ là một khối `admin-server` (FastAPI + SQLite/Postgres) gom auth, tài khoản, resource, thống kê. Dự án này **tái cấu trúc theo Strangler Pattern**
thành các service độc lập, mỗi service một database, chạy song song legacy cho tới khi chủ dự án duyệt cắt chuyển từng phần.

## 1.2 Bản đồ thành phần

```text
                     ┌────────────────────────── người dùng (browser) ──┐   ┌── Tool ngoài / Fallback Tool ──┐
                     │ manager-web (admin-web, React/Vite)              │   │ SDK: manager_sdk.IntegrationClient │
                     └───────────────┬──────────────────────────────────┘   └───────────────┬────────────────┘
                                     ▼  (nginx TLS ở production — chưa áp dụng)             ▼
                           ┌──────────────────────────────────────────────────────────────────┐
                           │ api-gateway :8400  (không DB; route theo route-map; edge security) │
                           └─┬─────────────┬───────────────────┬──────────────────┬───────────┘
                             ▼             ▼                   ▼                  ▼
                     identity :8430  integration :8431   analytics :8432     reward :8422
                     identity_db     integration_db      analytics_db(+Redis) reward_db
                             │             │ ▲                 ▲                  ▲
   feed kéo /internal/v1/feed│  (X-Service-Key, KHÔNG qua gateway)                 │
                             └─────────────┴─┴─────────────────┴──────────────────┘
```

| Thành phần | Thư mục | Cổng DEV | DB | Vai trò |
|---|---|---:|---|---|
| api-gateway | `services/api-gateway` | 8400 | — | Điểm vào công khai duy nhất; proxy ASGI; CORS/header/giới hạn/rate limit; không bao giờ route `/internal` |
| identity-service | `services/identity-service` | 8430 | `identity_db` | Nguồn sự thật account, đăng nhập/đăng ký, token RS256/JWKS, role, permission, invite key, OIDC |
| integration-service | `services/integration-service` | 8431 | `integration_db` | Cổng duy nhất cho Tool: client, credential, resource, snapshot/ingest, jobs, ACL cho API legacy |
| analytics-service | `services/analytics-service` | 8432 | `analytics_db` + Redis (tùy chọn) | Read model + toàn bộ thống kê Dashboard |
| reward-service | `reward-service/` (vị trí legacy) | 8422 | `reward_db` | Phân phối điểm thưởng qua Discord (engine job, SSE). Đã tách khỏi `accounts`/JWT secret chung |
| manager-web | `admin-web/` | 5174 (vite dev) | — | UI quản trị; gọi `/v1/*` qua gateway |
| Packages | `packages/{contracts,observability,sdk-python}` | — | — | Mã dùng chung, **chỉ generic** (xem 1.5) |
| Legacy còn chạy | `admin-server/`, `local-ops-hub/` | — | — | Hệ cũ; production vẫn chạy chúng. **Không sửa** trừ lỗi nghiêm trọng |

Tất cả bind `127.0.0.1` trong DEV.

## 1.3 Nguyên tắc kiến trúc (và vì sao)

| Nguyên tắc | Hệ quả cụ thể |
|---|---|
| Mỗi service sở hữu dữ liệu của nó | 4 DB + 4 user không đặc quyền; test chứng minh user A không kết nối được DB B (4×4) |
| Không chuỗi đồng bộ giữa service | Liên service dùng **feed kéo** (outbox) — một service chết không kéo service khác chết |
| Xác thực offline | Access token RS256, service verify bằng **JWKS** đã cache: Identity chết thì token đã cấp vẫn dùng được |
| Tool là *dữ liệu*, không phải *code* | Tên Tool/nền tảng/metric nằm trong Registry (bảng). Thêm Tool mới không sửa Manager (có test E2E chứng minh: Mock Tool X) |
| Ingest là hợp đồng dữ liệu, không phải "insert" | Upsert theo khóa tự nhiên, bản `observed_at` mới hơn thắng, metric tuyệt đối, `null ≠ 0`, commit snapshot nguyên tử |
| Dashboard chỉ đọc read model | Không đọc payload thô; read model dựng từ snapshot đã commit |
| Redis chỉ là tăng tốc | Cache-Aside, luôn có TTL; Redis chết → rơi về PostgreSQL |
| Strangler, không big-bang | Legacy giữ nguyên + route-map ánh xạ từng route cũ sang service đích; có parity test với OpenAPI/stats cũ |

Lý do chi tiết, các phương án đã loại, ma trận sở hữu: `architecture/architecture-assessment-v2.md`. Quyết định phát sinh: `architecture/implementation-decisions.md`.

## 1.4 Công nghệ

- Python 3.12, FastAPI, Pydantic v2, asyncpg (Postgres 16), httpx, PyJWT (`>=2.15.0`), cryptography (Fernet cho khóa ký trong DB), bcrypt (cost 12), redis-py.
- Frontend: React + Vite + TypeScript, Vitest + Testing Library, `npm` (lockfile). Không có thư viện state toàn cục; `FilterContext` tự viết.
- Test: pytest + pytest-asyncio; Postgres/Redis **thật** (DEV riêng), tiến trình thật qua `uvicorn`. Không mock DB.
- Hạ tầng DEV: Docker Compose project `manager-platform-dev`. Production: nginx + systemd/Docker (chưa dựng — xem runbook).

## 1.5 Cấu trúc repo

```text
CLAUDE.md                          luật ràng buộc (đọc đầu tiên)
infra/
  service-registry.json            NGUỒN SỰ THẬT: service, module, bảng sở hữu, event phát/nhận, cổng, DB, public_prefixes
  gateway/route-map.json           route legacy → service đích; render_nginx.py sinh nginx.conf
  docker/                          compose.dev.yml, templates/ (initdb, redis ACL) — render vào .dev/ (gitignored)
  scripts/                         bootstrap_dev, devenv, dev_stack, scaffold_service, test_all
services/<x>-service/              src/<package>/<module>/, migrations/, tests/, Dockerfile, .env.example, README.md
reward-service/                    legacy-in-place (đã refactor Phase 4), có DEPLOY.md
admin-web/                         manager-web (React)
admin-server/  local-ops-hub/      legacy — đóng băng
packages/contracts/                python/manager_contracts (ErrorCode, envelope) + ingest/<ngày>/ (JSON Schema + ví dụ)
packages/observability/            log JSON, request-id, health, app factory (chassis)
packages/sdk-python/               manager_sdk: TokenVerifier(JWKS), ServiceKeyAuth, FeedClient, IntegrationClient
tests/{arch,contract,integration,e2e}/
docs/{api,architecture,operations,handbook}/
```

**Package dùng chung chỉ được chứa mã generic** (luật 13). Không đặt logic nghiệp vụ vào `packages/`. Service **không import** service khác hay module legacy (test `tests/arch` quét).

## 1.6 Sổ đăng ký service (`infra/service-registry.json`)

Là nguồn sự thật mà test kiến trúc đọc: mỗi service khai `path, package, port, database, public_prefixes, modules, owned_tables, emits, consumes`.
- `status: scaffold` = service do `scaffold_service.py` quản lý (có chassis chuẩn) — **KHÔNG có nghĩa "chưa làm"**. Trạng thái làm việc thật nằm ở docstring `status:` của từng module (`planned|active`) và ở [02-trang-thai-tinh-nang.md](02-trang-thai-tinh-nang.md).
- `status: legacy-in-place` = reward-service (không bị scaffold đụng; `target_path` là nơi sẽ chuyển tới khi được duyệt).
- Test kiến trúc kiểm: bảng trong migration phải thuộc `owned_tables` của service; SQL không chạm bảng service khác; mọi route legacy có đúng một đích; v.v.

## 1.7 Chassis chuẩn của một service

Mọi service Python có: `/health/live`, `/health/ready` (**chỉ** kiểm tài nguyên của chính nó, timeout 1.5s/check, không gọi service khác/Redis), `/health/version`,
`/openapi.json`, envelope lỗi ổn định `{"ok":false,"error":{"code","message","details"},"meta":{request_id}}` (riêng các route tương thích legacy giữ `{"detail":...}`),
log JSON (kể cả log uvicorn) có `request_id`, config fail-fast từ env `<TÊN>_*`, Dockerfile chạy non-root, migration forward-only chạy bằng job riêng
(`python -m <package>.migrate`, **không** chạy lúc khởi động — ngoại lệ: reward có `REWARD_DB_MIGRATE_ON_BOOT=1`), test riêng.
