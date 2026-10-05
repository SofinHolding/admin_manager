# Báo cáo triển khai cuối — Manager Platform (microservices, Strangler)

Ngày: 2026-10-05 · Nhánh: `refactor/microservices` (chỉ commit cục bộ, **không push**, không deploy, không đụng VPS/CI/CD/production).
Cổng chất lượng: `python infra/scripts/test_all.py` — lần chạy đầy đủ (kèm `--legacy`) **xanh ở mọi bộ**; riêng 1 test phụ thuộc ngoài ghi ở "Chưa kiểm chứng".

## 1. Phase đã làm (ở repo)

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 0 | Tài liệu, baseline, khung service, test kiến trúc | Xong (còn: đọc nginx thật từ VPS — chỉ đọc, chưa làm) |
| 1.0 | `api-gateway` (ASGI proxy) + `infra/gateway/nginx.conf` sinh từ route-map | Xong ở repo. `nginx -t` thật + áp VPS: `PENDING_PRODUCTION_APPROVAL` |
| 1.1–1.2 | identity-service, port auth/accounts/keys, parity với legacy | Xong |
| 1.3 | Cutover single-writer Identity | **Chưa** — `PENDING_PRODUCTION_APPROVAL` (runbook có) |
| 1.4–1.7 | RS256/JWKS + phiên thu hồi được + permission + outbox; OIDC/PKCE + back-channel logout; đăng ký kèm integration | Xong |
| 2 | integration-service: clients/credentials/handshake/snapshot ingest atomic/idempotency/feed/provisioning | Xong |
| 2b | jobs/lease (Fallback Tool) + legacy ACL (26 operation `/v1/resources`, `/v1/ops`, `/v1/viewer/links`) | Xong (trừ canonicalizer `documents→snapshot`) |
| 3 | analytics-service: read model, consumer theo revision, FilterContext, cache-aside Redis, reconcile | Xong |
| 4 | reward-service bỏ bảng `accounts`/JWT secret chung; RS256 qua SDK; quyền qua Identity; feed consumer + denylist | Xong (`reward-service/` giữ vị trí) |
| 5 | Push Redis Streams tùy chọn | **Chưa** (đường mặc định — feed kéo + outbox — đã xong và đủ) |
| 6 | Retire legacy, bật lại CI/CD, chuyển `local-ops-hub` | **Chưa** — `PENDING_PRODUCTION_APPROVAL` |
| 7 | admin-web dùng `/v1/*` qua gateway, FilterContext, OIDC tùy chọn, hardening | Xong |

## 2. Topology DEV

```text
Browser / Tool ──► api-gateway :8400 (không DB) ──► identity :8430 → identity_db
                                                ├─► integration :8431 → integration_db
                                                ├─► analytics :8432 → analytics_db (+ Redis cache, tùy chọn)
                                                └─► reward :8422 → reward_db
Liên service: feed kéo `/internal/v1/feed` (X-Service-Key, outbox) — không đi qua gateway.
Hạ tầng DEV riêng: Postgres 127.0.0.1:55440 (project `manager-platform-dev`), Redis 127.0.0.1:56390; mỗi service một DB + một user không đặc quyền.
```
Dựng stack bằng tiến trình thật: `python infra/scripts/dev_stack.py up` (hoặc `DevStack` trong test).

## 3. Hợp đồng API
`docs/api/service-auth-contract-v1.md`, `internal-feed-contract-v1.md`, `integration-api-v1.md`, `analytics-api-v1.md`, `ingest-data-contract-v1.md`,
OpenAPI đóng băng `docs/api/openapi/{integration,analytics}-service.v1.json` (test chặn breaking change), schema ingest `packages/contracts/ingest/2026-10-01/`.

## 4. Migration
identity `0001–0004` (init, tokens/permissions/outbox, OIDC, registration/integration); integration `0001–0003` (init, jobs, legacy_ingest);
analytics `0001` (read models + sổ sách consumer); reward `001–004` (004: identity_link). Forward-only, job riêng (`python -m <pkg>.migrate`), không chạy lúc khởi động.

## 5. Kết quả kiểm thử (số thật, một lần chạy `test_all.py --legacy`)

| Bộ | Pass | Fail | Skip |
|---|---:|---:|---:|
| arch (ranh giới, cấu trúc, route map, hạ tầng, secret, 4×4 cô lập DB) | 117 | 0 | 0 |
| contract (ingest, parity Identity↔legacy, parity Analytics↔`stats_core`, OpenAPI đóng băng) | 38 | 0 | 0 |
| packages: contracts / observability / sdk-python | 5 / 23 / 44 | 0 | 0 |
| unit + integration theo service: identity | 439 | 0 | 0 |
| integration-service | 454 | 0 | 0 |
| analytics-service | 153 | 0 | 0 |
| reward-service | 180 | 0 | 0 |
| api-gateway | 452 | 0 | 1 (xem dưới) |
| integration (tiến trình thật, DB chết, log JSON) | 8 | 0 | 0 |
| **e2e (stack tiến trình thật qua gateway)** | **31** | 0 | 0 |
| legacy admin-server | 6 | 0 | 0 |
| frontend: typecheck / vitest (147) / build + quét bundle | đạt | 0 | 0 |

**Mock Tool X** (`tests/e2e/test_mock_tool_x.py`, 11 test): Tool mới hoàn toàn (tên "Mock Tool X", platform `toolx_social`): đăng ký user kèm integration → Identity phát event →
Integration tự tạo client → cấp credential → handshake → snapshot resources/content/events → commit → Analytics hiển thị đúng số (views, kênh, quốc gia, bộ lọc),
gửi lại = số tuyệt đối mới nhất (không cộng dồn), snapshot đầy đủ bỏ kênh thì kênh biến mất, user khác không thấy, key máy ≠ credential user, `/internal` không qua gateway.
**Không sửa một dòng code Manager**; test quét mã nguồn đảm bảo tên Tool/platform không xuất hiện ở đâu trong Manager. Duy nhất cần: admin thêm *dòng dữ liệu* Platform Registry (`PUT /v1/integrations/admin/platforms/{key}`).

**Cô lập lỗi** (`tests/e2e/test_z_fault_isolation.py`, tiến trình thật):
- dừng Analytics → đăng nhập + ingest vẫn chạy, Dashboard lỗi nhanh, khởi động lại thì tự đuổi kịp từ feed;
- dừng Reward → Auth/Integration/Analytics không ảnh hưởng;
- dừng Identity → token đã cấp vẫn verify OFFLINE (Analytics, Reward), ingest bằng API key vẫn chạy, login lỗi nhanh, sống lại tự khỏi;
- Redis không khả dụng → Analytics đọc thẳng PostgreSQL, ghi bền vẫn đúng, `/health/ready` không phụ thuộc Redis;
- DB của MỘT service chết (identity/integration/analytics) → service đó `ready=503` nhưng không crash, service không liên quan khỏe;
- truy cập DB chéo bị từ chối (12 cặp service→DB khác) + user service không phải superuser.

**Test của test (mutation):** mỗi agent chạy đột biến ngoài repo, kết quả cuối đều làm test ĐỎ rồi hoàn nguyên — gateway 13, identity OIDC 31, integration core 31, jobs/legacy 21, analytics 26, reward 7, web 1 (+ các đột biến bị lọt lúc đầu đã được siết test rồi chạy lại).

## 6. Tương thích legacy
- `/v1/resources/*`, `/v1/ops/*`, `/v1/viewer/links*`: Integration giữ nguyên đường dẫn/body/status/`{detail}` (parity với OpenAPI legacy, 26 operation, mỗi operation có test hành vi); ghi đi cùng đường canonical.
- Auth: Identity có chế độ HS256 tương thích khi legacy còn chạy (`IDENTITY_ACCESS_TOKEN_ALG=HS256`), mặc định RS256; parity 7 kịch bản.
- Analytics có alias `/v1/viewer/*`, `/v1/admin/country-*` với shape cũ.
- Khác biệt CÓ CHỦ ĐÍCH so với legacy (đã ghi trong docstring test/README): `owner*` trong body → 422 (legacy lặng lẽ bỏ qua); 422 không phản chiếu input; KPI `managed/active_channels` đếm kênh YouTube; bộ lọc áp cả `videos_posted`; nhập lại cùng `input_ref` trả resource cũ thay vì tạo bản trùng.

## 7. Lỗi bảo mật / độ bền đã phát hiện và sửa
- `admin-server` có JWT secret mặc định thật trong code → dev: tự sinh ngẫu nhiên; production từ chối khởi động (**đổi hành vi khi deploy — `PENDING_PRODUCTION_APPROVAL`**).
- Mật khẩu/secret test cứng → sinh ngẫu nhiên (`bootstrap_dev.py`), quét secret trong CI nội bộ.
- Đếm lần thử đổi mật khẩu bị rollback cùng transaction; NUL byte → 500; request treo 60s khi DB chết; readiness treo; log uvicorn không phải JSON.
- Outbox: thứ tự `seq` ≠ thứ tự commit làm consumer mất sự kiện → `pg_advisory_xact_lock` trước `nextval` (test đo mất mát, đỏ khi gỡ khóa).
- Deadlock giữa đường Tool và đường legacy ở Integration (thứ tự khóa advisory/dòng); `display_name` legacy vượt giới hạn canonical.
- Dependency: `pyjwt` nâng `>=2.15.0` (PYSEC-2026-4141); `npm audit` production sạch.
- Web: refresh token không còn trong `localStorage`; API key chỉ hiện một lần; cấm `innerHTML`; quét bundle không còn secret.
- Gateway: chặn `/internal` mọi biến thể, strip `X-Service-Key`/`X-Forwarded-*`, giới hạn body/header/URL, rate limit, circuit breaker, che token trong access log.

## 8. Giới hạn đã biết (trung thực)
1. **Chưa kiểm chứng:** `nginx -t` bằng nginx thật (ảnh `nginx:alpine` chưa có, không kéo về) — `NOT_RUN_EXTERNAL_DEPENDENCY`; Dockerfile của các service chưa build; chưa chạy trên VPS/CI.
2. **Khởi động lạnh khi Identity chết:** service chưa từng lấy JWKS sẽ trả 401 cho tới khi Identity sống lại (token đã cấp chỉ được verify offline khi JWKS đã được cache). Có thể thêm prefetch JWKS lúc khởi động — chưa làm.
3. **Thu hồi quyền ở service khác** bị chặn bởi TTL access token + feed (`session.revoked`); Reward/Integration có denylist theo feed. Thao tác nhạy cảm dùng introspection (D13).
4. **TTL access token RS256 hiện 900 s**, D12 yêu cầu 600 s — chưa đổi (cấu hình `IDENTITY_ACCESS_TOKEN_TTL_S`).
5. Rate limit và circuit breaker nằm trong bộ nhớ từng tiến trình (nhân lên khi nhiều worker).
6. Analytics: `read_resources` PK không gồm `resource_type`; seal cache toàn cục (commit của owner này làm mất cache owner khác); chưa single-flight chống thundering herd; `agg_daily` chưa dùng (tính từ read model, có cache).
7. Reward: thu quyền tài khoản role `discord` (shim) trả 409 cho tới khi Identity cấp `reward:access` tường minh; `session.revoked` dừng mọi job của tài khoản (thận trọng); `deploy-reward.yml` còn theo mô hình cũ.
8. Integration: canonicalizer `documents → snapshot` + DB role `legacy_ingest_writer` chưa làm (cần hạ tầng/sync server); SDK chưa có `heartbeat`/`lease_token`.
9. Web: chưa thử trên trình duyệt thật với stack thật (test bằng jsdom + mock fetch); `npm audit` còn 5 lỗ hổng **dev-only** (vite/vitest/esbuild) — sửa cần nâng major, chờ quyết định.
10. Mã lỗi `INTEGRATION_NOT_ALLOWED`, `INTEGRATION_LIMIT_EXCEEDED` và nhóm mã của Integration (`PAYLOAD_TOO_LARGE`, `EMAIL_NOT_VERIFIED`, ...) đang khai trong từng service, chưa đưa vào `manager_contracts.ErrorCode`.
11. Test của các service dùng chung `identity_db`/`integration_db` DEV: chạy song song nhiều bộ có thể làm nhau đỏ; `test_all.py` chạy tuần tự.

## 9. Việc production chờ duyệt (`PENDING_PRODUCTION_APPROVAL`)
Toàn bộ trong `production-cutover-runbook.md`: tạo DB/role/Redis ACL thật; cutover Identity; nginx + gateway; bật Integration/Analytics và backfill; di chuyển DB Reward và viết lại `deploy-reward.yml`;
build/deploy web; chuyển `local-ops-hub`; bật lại CI/CD (trigger `push`); gỡ legacy; xoay secret. Không bước nào đã được thực hiện.

## 10. Rollback / runbook
`docs/operations/production-cutover-runbook.md` (từng bước có quay lui), `docs/operations/identity-migration-runbook.md`, `reward-service/DEPLOY.md`, `infra/docker/README.md` (hạ tầng DEV), `docs/architecture/implementation-decisions.md` (ADR).
