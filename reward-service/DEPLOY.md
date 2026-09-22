# Triển khai reward-service

Module backend nằm **chung repo `admin_manager`** (thư mục `reward-service/`) nhưng chạy tiến trình
RIÊNG (pm2 `ufreward`, cổng `8422`), **dùng chung** Postgres `ufsync` + `JWT_SECRET` với admin-server
và **KHÔNG sửa một dòng nào của `admin-server/`** (backend admin). Giao diện discord đã được gộp vào
`admin-web` (phục vụ tại `/admin/`, hiển thị theo vai trò), nên KHÔNG còn site tĩnh `/reward/` riêng —
chỉ cần proxy API `/v1/reward/`. Deploy backend tự động qua `.github/workflows/deploy-reward.yml`
khi push `main` đụng `reward-service/**`; phần frontend do `deploy.yml` (paths `admin-web/**`) lo.

Topology trên VPS (cùng máy với admin_manager):

```
                          ┌────────────────────────────────────────┐
   nginx (443) ───/admin/─┼─▶ tĩnh admin-web dist (UI dùng chung,   │
                          │      hiển thị dashboard HOẶC UI discord  │
                          │      tuỳ role) + pm2 ufadmin :8421       │
              /v1/ ───────┼─▶ pm2 ufadmin  :8421   (admin-server)    │
          /v1/reward/ ────┼─▶ pm2 ufreward :8422   (reward-service)  │
                          └───────────────┬────────────────────────┘
                                          ▼
                              Postgres  ufsync  (dùng chung)
                    accounts (đọc + UPDATE role)  |  reward_* (sở hữu riêng)
```

## 1. Chuẩn bị lần đầu trên VPS (làm TAY, 1 lần)

```bash
sudo mkdir -p /srv/reward
sudo chown "$USER" /srv/reward
cd /srv/reward

# .env — CHMOD 600, KHÔNG commit. Copy JWT_SECRET NGUYÊN VĂN từ /srv/admin/.env.
cp <repo>/reward-service/.env.example .env
chmod 600 .env
# Sửa .env:
#   JWT_SECRET      = <giống hệt /srv/admin/.env — bắt buộc, check-env.py sẽ chặn nếu lệch>
#   DATABASE_URL    = postgres://<user>:<pass>@127.0.0.1:5432/ufsync   (đúng DB admin đang dùng)
#   REWARD_TOKEN_KEY= <python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())">
#                     ↑ SINH 1 LẦN, LƯU KỸ. Mất key = mất toàn bộ token Discord đã mã hoá.
#   REWARD_CORS_ORIGINS = https://<domain>    (origin thật của UI, không để localhost)
```

`REWARD_TOKEN_KEY` là bí mật RIÊNG của reward-service (admin_manager không biết) — dùng Fernet mã hoá
token Discord từng user trong `reward_user_credentials`. Sinh mới mỗi môi trường, không tái dùng.

## 2. Cấp role `discord` cho user (thao tác DỮ LIỆU, không sửa code admin)

`accounts.role` là cột `text` tự do (admin_manager chỉ ràng buộc regex `^(viewer|admin)$` ở tầng
pydantic khi *ghi qua API của nó* — không có CHECK ở DB). reward-service cấp quyền bằng UPDATE trực
tiếp, hoàn toàn hợp lệ:

```sql
UPDATE accounts SET role = 'discord' WHERE username = '<user>';
```

Hoặc trên `admin-web` (đăng nhập role `admin`): nút **Cấp/Thu hồi quyền Discord** ở trang
`/admin/manage` (tab *Tài khoản*), hoặc trang `/admin/reward/admin` — cả hai gọi
`POST /v1/reward/admin/accounts/{id}/grant-discord` của reward-service.

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

Push `main` đụng `reward-service/**` → workflow chạy:

1. **test**: dựng Postgres 16 service, `pytest tests/ -q` — fail thì DỪNG, không deploy.
2. **deploy**: rsync `reward-service/` (trừ `.env`, `tests/`, `.venv`) lên `/srv/reward/`, rồi trên VPS:
   - `scripts/check-env.py` — preflight: JWT_SECRET khớp `/srv/admin/.env`, Fernet hợp lệ, DB có
     bảng `accounts`. **Fail → không restart** (tránh chạy service cấu hình sai).
   - `python -m store.migrate` — áp `reward_*` migration (idempotent, chỉ `CREATE IF NOT EXISTS`).
   - `pm2 restart ufreward` (lần đầu: `pm2 start run.sh --name ufreward --interpreter bash`).
   - `curl /v1/reward/health` — smoke, fail → deploy coi như hỏng.

Dùng lại đúng secrets của admin_manager: `VPS_SSH_KEY`, `VPS_HOST`, `VPS_USER`. Paths filter đảm bảo
sửa reward **không** trigger deploy admin và ngược lại.

## 5. Vận hành

```bash
pm2 logs ufreward            # log runtime
pm2 restart ufreward         # restart tay
curl -s http://127.0.0.1:8422/v1/reward/health | jq   # db_ok / migration_version / admin_api_ok
```

`run.sh` cố định `--workers 1`: WorkerManager giữ trạng thái job + khoá `reward_job_lock` theo tiến
trình trong RAM. Chạy nhiều worker sẽ có 2 WorkerManager tranh cùng job, phá tầng chống trùng L3.
Muốn scale phải tách máy + hàng đợi, **không** tăng `--workers`.
