# Runbook chuyển production — TẤT CẢ BƯỚC LÀ `PENDING_PRODUCTION_APPROVAL`

> **Không bước nào dưới đây đã được thực hiện.** Repo này chỉ chứa mã, script và tài liệu. Không có deploy, không sửa nginx/VPS,
> không đổi traffic, database, credential, không xóa legacy, không `git push`, không bật CI/CD. Mỗi bước cần người dùng duyệt RIÊNG bằng văn bản.
> Chi tiết riêng cho Identity: `identity-migration-runbook.md`.

Nguyên tắc: Strangler — mỗi bước thêm đường mới **song song** với legacy, có kiểm chứng (parity/shadow), có điểm quay lui. Legacy chỉ bị gỡ ở bước cuối
và chỉ sau một thời gian chạy ổn định do người dùng quyết định.

## 0. Điều kiện chung trước mọi bước
- [ ] `python infra/scripts/test_all.py` xanh trên commit sẽ triển khai (lưu log kèm commit SHA).
- [ ] Sao lưu: `pg_dump` các DB legacy (ufsync/admin) + snapshot VPS; lưu NGOÀI VPS; thử restore trên máy dev.
- [ ] Nginx production hiện tại được **đọc** (chỉ đọc) và so với `infra/gateway/nginx.conf` (`python infra/gateway/render_nginx.py --check`).
- [ ] Cửa sổ bảo trì + người trực + kênh liên lạc.

## 1. Hạ tầng dữ liệu riêng (`PENDING_PRODUCTION_APPROVAL`)
1. Tạo 4 database + 4 user KHÔNG đặc quyền (identity/integration/analytics/reward) từ mẫu `infra/docker/templates/initdb/01-databases.sql.tpl`
   (mật khẩu sinh ngẫu nhiên, lưu secret manager — không để trong repo/CI).
2. Redis: ACL user `analytics_cache`, prefix `analytics:*`, `default off`, `-@dangerous` (mẫu `users.acl.tpl`). Redis chỉ là cache.
3. Chạy `python -m <service>.migrate` cho từng service (job riêng, không chạy lúc khởi động). Kiểm `schema_migrations`.
- Rollback: xóa DB/role mới (chưa có traffic). Dry-run: chạy toàn bộ trên Postgres staging.

## 2. Identity (Phase 1.3 cutover) — xem `identity-migration-runbook.md`
Shadow sync legacy→identity_db (`legacy_sync.py`) → parity → single-writer cutover → RS256/JWKS. Cần: sinh `IDENTITY_KEY_ENCRYPTION_KEY` (Fernet) lưu ngoài DB;
`python -m identity_service.keys rotate`; đăng ký OIDC client (`oidc_clients`); khóa feed `IDENTITY_FEED_KEY_<READER>` mỗi bên một khóa.
- Quay lui: trỏ route auth về legacy (route-map), giữ `HS256` compat (`IDENTITY_ACCESS_TOKEN_ALG=HS256`) trong thời gian cùng sống.

## 3. Gateway + nginx (Phase 1.0)
1. Chạy `api-gateway` bind 127.0.0.1:8400 (`--no-proxy-headers --no-server-header --no-access-log`, `GATEWAY_TRUSTED_PROXIES=127.0.0.1`, `GATEWAY_CORS_ORIGINS` đúng origin).
2. `nginx -t` với `infra/gateway/nginx.conf` (chưa từng chạy bằng nginx thật — `NOT_RUN_EXTERNAL_DEPENDENCY`), rồi reload. `/internal/*` phải bị chặn (kiểm bằng curl).
3. Chuyển dần từng nhóm route theo `route-map.json` (đọc → ghi), theo dõi log/lỗi từng nhóm.
- Quay lui: khôi phục file nginx cũ (đã sao lưu) + reload.

## 4. Integration + Analytics (Phase 2–3)
1. Khởi động integration-service; seed Platform/Metric Registry (migration đã seed); provisioning consumer kéo feed Identity.
2. Cấp credential cho Tool qua UI/API; Tool dùng `IntegrationClient` (SDK) — tài liệu: `docs/api/integration-api-v1.md`, `ingest-data-contract-v1.md`.
3. Analytics: chạy `admin/rebuild` từ canonical (backfill từ revision 0) → đối chiếu `reconcile` (checksum) → bật route Dashboard.
4. Chạy shadow so sánh số liệu analytics với `stats_core` legacy (parity test đã có) trên dữ liệu thật **chỉ đọc**.
- Quay lui: route `/v1/analytics/*` về legacy; dữ liệu canonical giữ nguyên.

## 5. Reward (Phase 4)
Di chuyển bảng `reward_*` từ DB chung sang `reward_db` (kế hoạch ở `services/reward-service/DEPLOY.md`); đặt `REWARD_DATABASE_URL`, `REWARD_IDENTITY_*`, `REWARD_IDENTITY_FEED_KEY`;
mang theo `manager_sdk` (`REWARD_SDK_PATH` hoặc `vendor/`). Gỡ `JWT_SECRET` chung khỏi reward. `deploy-reward.yml` cần viết lại theo mô hình mới (đang theo mô hình cũ).
- Quay lui: dừng reward mới, bật reward cũ trên DB cũ (giữ nguyên bản sao DB cũ cho tới khi ổn định).

## 6. Frontend + Fallback Tool (Phase 6–7)
Build `admin-web` (`npm run build`, `scan-bundle` sạch), đặt `VITE_AUTH_MODE` (`legacy` | `oidc`), đăng ký redirect URI chính xác. `local-ops-hub` chuyển sang machine credential + jobs API (`IntegrationClient`).

## 7. Bật lại CI/CD (chỉ theo yêu cầu rõ ràng)
Workflow deploy hiện chỉ có `workflow_dispatch` (test `tests/arch/test_ci_guard.py` giữ điều này). Bật lại trigger `push` cùng commit sửa test guard,
sau khi tất cả bước trên được duyệt. Quy ước: không deploy khi `test_all.py` đỏ.

## 8. Gỡ legacy (cuối cùng, `PENDING_PRODUCTION_APPROVAL`)
Chỉ sau thời gian ổn định do người dùng quyết định: xóa route legacy khỏi `route-map.json`, gỡ `admin-server`/bảng cũ. **Không làm tự động**; cần bản sao lưu cuối và duyệt riêng.

## 9. Xoay secret / khóa (định kỳ)
- Khóa ký token: `python -m identity_service.keys rotate` (khóa cũ vẫn publish trong `IDENTITY_JWKS_GRACE_SECONDS`).
- Khóa feed / service key: đổi env cả hai đầu → khởi động lại (ADR-010).
- API key của Tool: rotate qua `POST /v1/integrations/clients/{id}/credentials/{key_id}/rotate`.

## Điểm yếu đã biết cần cân nhắc khi duyệt
Xem mục "Giới hạn đã biết" của `implementation-final-report.md`.
