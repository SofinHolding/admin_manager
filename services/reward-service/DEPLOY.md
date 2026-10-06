# Triển khai reward-service

> **Phase 4 (tài liệu — chưa triển khai gì):** Reward KHÔNG còn dùng chung Postgres `ufsync` và `JWT_SECRET` với
> admin-server. Nó chạy trên **DB riêng `reward_db`** (user `reward_svc`), verify access token **RS256 của Identity
> bằng JWKS (offline)**, enforce quyền cục bộ từ claim `perms`, và nhận thay đổi quyền/phiên qua **feed nội bộ** của
> Identity. Phần dưới đã cập nhật; mọi bước dưới đây chỉ thực hiện khi có lệnh triển khai riêng.

Module backend nằm **chung repo** (thư mục `services/reward-service/`) nhưng chạy tiến trình RIÊNG (pm2 `ufreward`, cổng
`8422`). Giao diện discord nằm trong `admin-web` (phục vụ tại `/admin/`); chỉ cần proxy API `/v1/reward/`.

Topology:

```
   nginx (443) ── /v1/reward/ ──▶ pm2 ufreward :8422 (reward-service)
                                        │  verify JWKS (cache, offline)      ┌────────────────────────────┐
                                        ├───────── HTTP công khai ──────────▶│ identity-service :8430      │
                                        │  /v1/auth/*, /v1/admin/users,      │  /.well-known/jwks.json     │
                                        │  PUT|DELETE …/permissions/…        │  /internal/v1/feed (pull)   │
                                        └───────── kéo feed (X-Service-Key) ─┘
                                        ▼
                              Postgres  reward_db  (RIÊNG; user reward_svc)
```

Reward không đọc/ghi bảng của Identity. Identity chết: token còn hạn vẫn được phục vụ (JWKS đã cache), job đang chạy
không bị ảnh hưởng; chỉ grant/revoke và proxy đăng nhập trả `503 UNAVAILABLE`.

## 1. Chuẩn bị lần đầu trên VPS (làm TAY, 1 lần)

```bash
sudo mkdir -p /srv/reward && sudo chown "$USER" /srv/reward && cd /srv/reward
cp <repo>/reward-service/.env.example .env && chmod 600 .env   # KHÔNG commit .env thật
```

Biến môi trường (xem `.env.example`; **không có secret mặc định**, thiếu biến bắt buộc ⇒ service không khởi động):

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `REWARD_DATABASE_URL` | có | DSN DB **riêng** `reward_db` (user `reward_svc`). **Không** fallback `DATABASE_URL` cũ |
| `REWARD_TOKEN_KEY` | có | Fernet mã hoá token Discord. Sinh 1 lần, lưu kỹ — mất key = mất token Discord đã mã hoá |
| `REWARD_IDENTITY_URL` | có | URL API công khai Identity (vd `http://127.0.0.1:8430`) |
| `REWARD_IDENTITY_JWKS_URL` | không | mặc định `${REWARD_IDENTITY_URL}/.well-known/jwks.json` |
| `REWARD_ISSUER` | nên đặt | phải khớp CHÍNH XÁC claim `iss` (mặc định = `REWARD_IDENTITY_URL`) |
| `REWARD_AUDIENCE` | không | mặc định `manager-api` |
| `REWARD_IDENTITY_FEED_URL` / `REWARD_IDENTITY_FEED_KEY` | nên có | nguồn + khoá feed. **Thiếu key ⇒ consumer tắt** (thu quyền/khoá chỉ có hiệu lực khi token hết hạn) |
| `REWARD_IDENTITY_TIMEOUT_S` / `_RETRIES` / `_BACKOFF_S` | không | outbound tới Identity: 5s / 2 lần / 0.3s |
| `REWARD_FEED_POLL_INTERVAL_S` / `_BATCH_SIZE` / `_EVENT_MAX_ATTEMPTS` | không | 2s / 100 / 4 |
| `REWARD_SDK_PATH` | không | thư mục chứa `manager_sdk` nếu không có `../packages/sdk-python` hoặc `./vendor` |

**Đã BỎ:** `JWT_SECRET`, `ADMIN_API_BASE`, `DATABASE_URL` (nếu còn trong `.env` sẽ bị bỏ qua; `scripts/check-env.py` cảnh báo).
Khoá feed do `infra/scripts/bootstrap_dev.py` sinh ở DEV (`IDENTITY_FEED_KEY_REWARD`); production dùng khoá riêng cho Reward.

`manager_sdk` (`packages/sdk-python`) không phải gói PyPI: bản deploy phải mang theo thư mục này (đặt cạnh
`services/reward-service/` theo cấu trúc monorepo, hoặc sao chép vào `services/reward-service/vendor/manager_sdk`, hoặc đặt `REWARD_SDK_PATH`).

## 2. Quyền reward do Identity quản lý (không còn UPDATE bảng accounts)

- Truy cập: permission **`reward:access`**; quản trị: **`reward:admin`** (hoặc `role=admin`). Tài khoản `role=discord` cũ được
  Identity ngầm cấp `reward:access` (shim, bỏ ở Phase 6).
- Cấp/thu: trên `admin-web` (nút Cấp/Thu hồi quyền Discord) hoặc gọi `POST /v1/reward/admin/accounts/{id}/grant-discord|revoke-discord`
  — Reward chuyển thành `PUT|DELETE /v1/admin/accounts/{id}/permissions/reward:access` của Identity bằng chính token người gọi.
  Thu quyền tài khoản role=discord (chỉ có quyền ngầm) trả `409` cho tới khi Identity chuyển nó sang permission tường minh.
- Thu quyền/khoá/xoá/thu hồi phiên: Identity phát event → Reward ghi `account_denylist`, **tạm dừng ngay** job đang chạy của
  tài khoản đó và từ chối job mới (không đợi token hết hạn).
- Dead-letter feed: `python -m identity_link.consumer replay` (chạy trong thư mục `reward-service`, cần env như service).

## 2b. Di chuyển dữ liệu từ `ufsync` sang `reward_db` (kế hoạch, làm TAY khi được duyệt)

Các bảng `reward_*` hiện nằm trong `ufsync`. Cutover: dừng `ufreward` → `pg_dump --data-only -t 'reward_*'` từ `ufsync` →
restore vào `reward_db` (đã `python -m store.migrate`) → đặt `.env` mới → start. Không cần dữ liệu `accounts`: `account_id` trong
`reward_*` là id tài khoản của Identity (giữ nguyên giá trị). `account_projection` tự dựng lại khi consumer replay feed từ 0.

## 3. nginx — thêm 1 location vào server block đang phục vụ admin_manager

**KHÔNG sửa** các location `/admin/` và `/v1/` sẵn có. UI discord nằm trong `admin-web` (đã có
`/admin/`), nên chỉ cần THÊM proxy API reward:

```nginx
# ── API reward-service ──────────────────────────────────────────────────────
location /v1/reward/ {
    proxy_pass http://127.0.0.1:8422;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE (GET /v1/reward/jobs/{id}/stream): PHẢI tắt buffering + tắt timeout đọc,
    # nếu không nginx sẽ gom event và client không nhận realtime.
    proxy_buffering    off;
    proxy_cache        off;
    proxy_read_timeout 3600s;
    chunked_transfer_encoding on;
}
```

> Location `/v1/reward/` PHẢI đứng TRƯỚC `/v1/` (khớp cụ thể hơn) để không bị route nhầm sang
> admin-server. Reload: `sudo nginx -t && sudo systemctl reload nginx`.

## 4. Deploy tự động (CI/CD)

Push `main` đụng `services/reward-service/**` → workflow chạy:

1. **test**: cần Postgres có DB `reward_db` + user `reward_svc` (qua `REWARD_TEST_DATABASE_URL`), `pytest tests/ -q` — fail thì DỪNG. (Workflow CI hiện còn theo mô hình cũ — cần cập nhật riêng.)
2. **deploy**: rsync `services/reward-service/` (trừ `.env`, `tests/`, `.venv`) lên `/srv/reward/`, rồi trên VPS:
   - `scripts/check-env.py` — preflight: Fernet hợp lệ, `REWARD_DATABASE_URL` kết nối được và **không** có bảng của
     Identity (tránh trỏ nhầm DB dùng chung), cấu hình Identity hợp lệ, JWKS lấy được (chỉ cảnh báo). **Fail → không restart**.
   - `python -m store.migrate` — áp `reward_*` migration (idempotent, chỉ `CREATE IF NOT EXISTS`).
   - `pm2 restart ufreward` (lần đầu: `pm2 start run.sh --name ufreward --interpreter bash`).
   - `curl /v1/reward/health` — smoke, fail → deploy coi như hỏng.

Dùng lại đúng secrets của admin_manager: `VPS_SSH_KEY`, `VPS_HOST`, `VPS_USER`. Paths filter đảm bảo
sửa reward **không** trigger deploy admin và ngược lại.

## 5. Vận hành

```bash
pm2 logs ufreward            # log runtime
pm2 restart ufreward         # restart tay
curl -s http://127.0.0.1:8422/v1/reward/health | jq   # ok / migration_version / feed{enabled,cursor,last_error}
```

`run.sh` cố định `--workers 1`: WorkerManager giữ trạng thái job + khoá `reward_job_lock` theo tiến
trình trong RAM. Chạy nhiều worker sẽ có 2 WorkerManager tranh cùng job, phá tầng chống trùng L3.
Muốn scale phải tách máy + hàng đợi, **không** tăng `--workers`.
