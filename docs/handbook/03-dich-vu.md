# 3. Chi tiết từng service

Mỗi mục: mục đích · chạy · biến môi trường · module · bảng · endpoint · event · điểm tinh tế · test. Endpoint đầy đủ (schema/ví dụ/lỗi) ở OpenAPI và `docs/api/*`;
ở đây chỉ nêu nhóm endpoint + hành vi cần biết. Biến môi trường thật nằm ở `.env.example` của từng service; **không** có secret trong repo (DEV sinh bởi `bootstrap_dev.py`).

Quy ước chung: env có tiền tố `<TÊN>_` (`IDENTITY_`, `INTEGRATION_`, `ANALYTICS_`, `REWARD_`, `GATEWAY_`); config sai/thiếu ⇒ **thoát với lỗi** (fail-fast), không lộ giá trị; migration chạy bằng job riêng.
Chạy một service: `cd services/<x>-service && python -m <package>.migrate && python -m uvicorn <package>.main:app --port <cổng>` (với env đã nạp từ `.env.dev`),
hoặc dùng `infra/scripts/dev_stack.py` (nạp env + migrate + chạy cả bộ).

---

## 3.1 identity-service (`:8430`, `identity_db`, package `identity_service`)

**Là nguồn sự thật duy nhất** cho account, đăng nhập/đăng ký, token, role, permission, invite key, OIDC.

**Biến môi trường chính:** `IDENTITY_DATABASE_URL` (bắt buộc), `IDENTITY_KEY_ENCRYPTION_KEY` (Fernet — mã hóa khóa ký private trong DB; **mất khóa này = mất khả năng ký**), `IDENTITY_ISSUER`,
`IDENTITY_AUDIENCE` (mặc định `manager-api`), `IDENTITY_ENV` (`dev|test|production`; dev in token email ra log), `IDENTITY_ACCESS_TOKEN_ALG` (`RS256` mặc định; `HS256` chỉ để sống chung legacy, cần `IDENTITY_JWT_COMPAT_SECRET`),
`IDENTITY_ACCESS_TOKEN_TTL_S` (mặc định 900), `IDENTITY_JWKS_GRACE_SECONDS`, `IDENTITY_FEED_KEY_<READER>` (mỗi bên đọc feed một khóa ≥24 ký tự: `ANALYTICS`, `INTEGRATION`, `REWARD`),
`IDENTITY_INTEGRATION_FEED_URL/KEY` (consumer provisioning; thiếu ⇒ tắt, service vẫn chạy), `IDENTITY_WEB_REDIRECT_URIS`, `IDENTITY_BACKCHANNEL_*`, `IDENTITY_SSO_TTL_S`, `IDENTITY_AUTH_CODE_TTL_S`.

| Module | Việc |
|---|---|
| `auth` | register/login/logout/refresh/verify-email/forgot/reset/change-password (giữ contract legacy); `authenticate()` dùng chung với OIDC; rate limit đăng nhập trong bộ nhớ; hash giả chống dò username qua thời gian |
| `accounts` | hồ sơ, admin user, trạng thái; `integration_repository` (provisioning của tài khoản) |
| `registration_keys` | invite key; `policy.py`: `normalize_policy` (admin tạo key) + `plan_registration` (kiểm `requested ⊆ policy` **trước** khi tiêu key) |
| `tokens` | `KeyStore` (khóa active duy nhất — chỉ mục unique một phần; khóa retired vẫn publish trong ân hạn), `TokenService` (`sign`, `verify_own`; access token bắt buộc `typ=at+jwt`), JWKS |
| `permissions` | catalog (`admin_only`), cấp/thu, `perms` trong token, shim `discord`→`reward:access` |
| `oidc` | discovery, authorize(+PKCE S256 bắt buộc, `state` bắt buộc), login hosted (CSRF, cookie SSO HttpOnly), token, userinfo, introspect (client phải xác thực), revoke, logout (open-redirect an toàn), back-channel (IP đã kiểm duyệt, chống DNS rebinding, không theo redirect), registry client (`oidc_clients.py seed-dev`) |
| `outbox` | ghi event trong cùng transaction; `/internal/v1/feed` |
| `audit` | `auth_audit` |
| `notifications` | `SmtpMailer` / `LogMailer` (port/adapter) |
| `feed_consumer` | kéo feed Integration → cập nhật `integration_provisioning` (idempotent, cursor bền, dead-letter, replay: `python -m identity_service.feed_consumer replay`) |
| (gốc) `legacy_sync.py`, `keys.py` | đồng bộ một chiều legacy→identity_db + parity; CLI `keys rotate|list` |

**Bảng sở hữu (20):** `accounts, invite_keys, refresh_tokens, permissions, account_permissions, oidc_clients, signing_keys, auth_audit, outbox, sessions, integration_provisioning, feed_cursor, processed_events, dead_letters, oidc_auth_codes, oidc_refresh_tokens, sso_sessions, oidc_session_clients, oidc_backchannel_deliveries, oidc_cursor`.
Migration: `0001_init` · `0002_tokens_permissions_outbox` · `0003_oidc` · `0004_registration_integration`.

**Endpoint:** `/v1/auth/*` (legacy-compatible) · `/v1/accounts/me/integration`, `/v1/accounts/{id}/permissions` · `/v1/admin/{users,keys,permissions,accounts/{id}/permissions/{perm},oidc/clients}` ·
`/.well-known/{jwks.json,openid-configuration}` · `/oauth/{authorize,login,token,userinfo,introspect,revoke,logout}` · nội bộ `/internal/v1/feed` (không qua gateway, `X-Service-Key`).

**Event phát:** `account.registered` (kèm `provisioning{client_id,tool_name,client_kind,platforms,scopes}` nếu đăng ký kèm Tool), `account.verified`, `account.status_changed`, `account.role_changed`, `account.profile_updated`, `account.permission_changed`, `account.deleted`, `session.revoked`.
**Event nhận:** `integration.client.provisioned`, `integration.client.provision_failed`.

**Điểm tinh tế:**
- Phiên SSO cũng là một dòng `sessions` (`client_type='sso'`) nên đổi mật khẩu/khóa/đổi role/thu hồi làm chết cả cookie SSO. Đăng nhập lại luôn cấp cookie mới (chống session fixation).
- Auth code bị dùng lại ⇒ thu hồi cả family + phiên + refresh và phát `session.revoked`. Hai refresh đồng thời cùng một token bị coi là dùng lại (chặt chẽ có chủ đích).
- Đăng ký kèm integration chạy trong **một transaction** (có test gài trigger làm lỗi bước cuối để chứng minh rollback). Không yêu cầu `integration` ⇒ response y hệt legacy.
- Thu hồi một access token ⇒ kết thúc cả grant (token gắn `sid`).
- Log truy cập uvicorn che query nhạy cảm (`id_token_hint`, `code`, `token`…) bằng `logredact.py`.
- Test dùng chung `identity_db` DEV: **không chạy song song** hai bộ test identity.

---

## 3.2 integration-service (`:8431`, `integration_db`, package `integration_service`)

**Cổng duy nhất cho Tool/hệ thống ngoài.** Hai mặt phẳng xác thực tách biệt: USER (JWT RS256 qua JWKS) cho quản trị; MÁY (API key `mpk_`) cho ingest/jobs. Xem [05](05-xac-thuc-va-bao-mat.md).

**Biến môi trường chính:** `INTEGRATION_DATABASE_URL`, `INTEGRATION_ISSUER`, `INTEGRATION_AUDIENCE`, `INTEGRATION_IDENTITY_JWKS_URL`, `INTEGRATION_FEED_KEY_<READER>` (`ANALYTICS`, `IDENTITY`),
`INTEGRATION_IDENTITY_FEED_URL/KEY` (consumer provisioning; thiếu ⇒ tắt), `INTEGRATION_RATE_LIMIT_PER_MIN` (600), `INTEGRATION_SNAPSHOT_TTL_S` (21600), `INTEGRATION_MAX_BODY_BYTES` (2 MiB, hỗ trợ gzip),
`INTEGRATION_MAX_CLIENTS_PER_OWNER` (20), `INTEGRATION_MAX_CREDENTIALS_PER_CLIENT` (10), `INTEGRATION_CREDENTIAL_DEFAULT_TTL_DAYS`, `INTEGRATION_MAINTENANCE_INTERVAL_S` (60), `INTEGRATION_JOB_*`, `INTEGRATION_LEGACY_REDEEM_*`.

| Module | Việc |
|---|---|
| `clients`, `credentials`, `installations` | client ↔ account; API key (hash, hiện 1 lần, xoay, thu hồi); handshake + thiết bị. Scope mặc định: `integration:handshake, resources:write, snapshots:write, metrics:write`; fallback: `jobs:claim, jobs:complete`; `jobs:claim:system` chỉ admin cấp |
| `platforms` | Platform Registry + Metric Registry — **dữ liệu**, thêm bằng `PUT /v1/integrations/admin/{platforms,metrics}/{key}` (role admin) |
| `projects`, `resources` | project/source namespace (tự upsert), resource (page/channel/profile), `execution_mode`, `origin` |
| `snapshots`, `ingest` | state machine `building → committing → committed | failed | abandoned`; `ingest.validate/merge` (LWW, absolute, null≠0, removed suy luận, events bất biến); commit nguyên tử |
| `idempotency` | `Idempotency-Key` tùy chọn ở mọi endpoint; giữ chỗ nằm trong transaction nghiệp vụ; lưu text để phát lại đúng byte |
| `feed` | outbox + `/internal/v1/feed` + `/internal/v1/canonical/{kind}` + `/summary` (checksum đối soát) |
| `jobs` | hàng đợi Fallback Tool: claim `FOR UPDATE SKIP LOCKED`, lease ≤900s, heartbeat, retry/backoff, `pending|leased|done|failed|dead|cancelled` |
| `legacy_acl` | 26 operation `/v1/resources`, `/v1/ops`, `/v1/viewer/links` giữ shape legacy; dịch sang cùng đường ghi canonical |
| `workers` | đăng ký/heartbeat worker (fallback_tool); in-memory, không phát event |
| `channel_groups` | `user_channel_groups` — viewer tự quản lý nhóm quốc gia; CRUD `/v1/user/channel-groups` |
| `audit` | `audit_log` |
| (gốc) `maintenance.py`, `machine_runner.py`, `ratelimit.py`, `security.py` | worker bảo trì (lease hết hạn, snapshot kẹt `committing` >10 phút → failed, `building` quá TTL → abandoned); chạy machine plane; chặn xác thực sai theo IP (30/phút) |

**Bảng sở hữu (32):** `integration_clients, integration_credentials, installations, platform_registry, metric_registry, projects, resources, resource_project_bindings, snapshots, snapshot_items, content_items, daily_metrics, activity_events, idempotency_keys, jobs, audit_log, account_projection, outbox, feed_cursor, processed_events, dead_letters, user_channel_groups` + nhóm `legacy_ingest`:
`documents, user_meta, account_links, link_codes, platform_policies, resource_sources, resource_assignments, operation_batches, operation_items, operation_audit_log`.
Migration: `0001_init` · `0002_jobs` · `0003_legacy_ingest` · `0004_projection_status_seq` · `0005_channel_subscriptions` · `0006_delegated_ingestion` · `0007_user_channel_groups` (nhóm quốc gia viewer).

**Endpoint (máy, `Authorization: Bearer mpk_…`):** `POST /v1/integrations/handshake`, `PUT /projects/{key}`, `POST /resources/batch`, `POST /snapshots`, `POST /snapshots/{id}/{resources|content|metrics|events|commit}`, `GET /snapshots/{id}`,
`POST /jobs/claim`, `/jobs/{id}/{complete|fail|heartbeat}`.
**Endpoint (user, token RS256):** `/v1/integrations/clients[...]` (+ `/credentials`, `/rotate`), `/audit`, `/admin/{platforms,metrics,resources}`, `/jobs` (tạo/liệt kê/hủy). **Legacy (user token):** `/v1/resources/*`, `/v1/ops/*`, `/v1/viewer/links*`. **Nội bộ:** `/internal/v1/{feed,canonical/{kind},canonical/summary}`.

**Event phát:** `snapshot.committed`, `resource.changed`, `integration.client.provisioned`, `integration.client.provision_failed`, `credential.revoked`. **Nhận:** `account.registered|verified|status_changed|deleted` (→ bảng `account_projection`).

**Điểm tinh tế:**
- **Ownership suy ra từ credential.** Bất kỳ `owner*` nào ở body ingest/legacy ⇒ `422 RECORD_VALIDATION_FAILED` cho cả request.
- Tạo credential cần `email_verified` của owner (`account_projection`; thiếu projection = coi chưa xác thực) ⇒ `EMAIL_NOT_VERIFIED`. Đây là lý do E2E phải xác thực email trước khi lấy API key.
- `seq` (outbox) và `revision` (canonical) cấp dưới **advisory lock lấy trước khi cấp, giữ tới commit**; khóa advisory phải lấy **trước** khóa dòng (đã từng deadlock giữa đường Tool và đường legacy).
- Trang `canonical/{kind}` không được cắt ngang một revision (có thể dài hơn `limit`); mỗi item phải có `owner_account_id` và `platform`.
- Checksum `canonical/summary` = sha256 các dòng đã sort (byte, `COLLATE "C"`) `<khóa tự nhiên>|<revision>|<removed 0/1>` (+ số liệu hiển thị); khóa tự nhiên **gồm `resource_type`** (xem §4.4) — **Analytics đối soát theo đúng công thức này; đổi là phá reconcile**. Integration tính bằng SQL trên cột của chính nó (không dùng `id` nội bộ); test xuyên hai service `tests/contract/test_integration_analytics_reconcile.py` chạy dữ liệu Integration thật qua consumer thật rồi `reconcile` phải `ok`.
- Snapshot `full` chỉ suy luận `removed` cho bản ghi có `observed_at` **<= observed_at của snapshot** (bản ghi mới hơn do batch/delta ghi trong lúc snapshot đang building là bằng chứng Tool còn thấy nó). Bản ghi con (content/metrics/events) không gắn được vào resource đã `removed` (`RESOURCE_UNKNOWN`: gửi lại resource để hồi sinh) và không đoán khi nhiều `resource_type` cùng `external_id` còn sống.
- Tool đẩy một khóa resource ⇒ Tool **tiếp quản** (`origin='tool'`, kể cả resource nhập tay trước đó và kể cả dữ liệu y hệt): Legacy chỉ thấy/sửa `origin<>'tool'` nên không còn ghi đè dữ liệu Tool.
- Xoay/thu hồi credential: chỉ chủ client hoặc role `admin` (`dash_admin` chỉ xem ⇒ 403 `SCOPE_DENIED`).
- Replay dead-letter của provisioning: `account.registered` cũ chỉ điền chỗ trống của `account_projection`; `account.status_changed` cũ bị bỏ nếu `status_seq` đã mới hơn (migration `0004`).
- Snapshot `full` cũ hơn snapshot đã commit chỉ bỏ phần suy luận `removed` (warning `SNAPSHOT_SUPERSEDED`), không lật `current` lùi. Resource nhập tay (`origin<>'tool'`) không bị snapshot `full` của Tool xóa.
- Mã lỗi riêng của service khai ở `integration_service/errors.py::Extra` (chưa có trong `manager_contracts.ErrorCode`): `PAYLOAD_TOO_LARGE, EMAIL_NOT_VERIFIED, CLIENT_NOT_ACTIVE, ACCOUNT_INACTIVE, CLIENT_NOT_FOUND, CREDENTIAL_NOT_FOUND, LIMIT_EXCEEDED, SNAPSHOT_NOT_WRITABLE, SNAPSHOT_COMMIT_IN_PROGRESS, UNSUPPORTED_ENCODING, JOB_INVALID_STATE, JOB_NOT_ELIGIBLE`.
- OpenAPI đóng băng ở `docs/api/openapi/integration-service.v1.json`; **thêm path = additive OK; đổi/xóa = breaking ⇒ contract version mới** (test `tests/contract/test_integration_openapi_frozen.py`).

---

## 3.3 analytics-service (`:8432`, `analytics_db`, Redis tùy chọn, package `analytics_service`)

**Chỉ đọc read model** dựng từ dữ liệu canonical của Integration; không bao giờ đọc payload thô.

**Biến môi trường chính:** `ANALYTICS_DATABASE_URL`, `ANALYTICS_ISSUER`, `ANALYTICS_AUDIENCE`, `ANALYTICS_IDENTITY_JWKS_URL`, `ANALYTICS_INTEGRATION_URL` + `ANALYTICS_INTEGRATION_FEED_KEY`, `ANALYTICS_IDENTITY_URL` + `ANALYTICS_IDENTITY_FEED_KEY`
(thiếu URL/khóa ⇒ consumer tắt, Dashboard vẫn đọc dữ liệu đã có), `ANALYTICS_REDIS_URL` (trống = không cache), `ANALYTICS_CACHE_TTL_S` (300), `ANALYTICS_STALE_AFTER_S` (3600), `ANALYTICS_SYNC_INTERVAL_S` (5), `ANALYTICS_CONSUMER_MAX_ATTEMPTS`, `ANALYTICS_CONSUMER_BACKOFF_BASE_S`.

| Module | Việc |
|---|---|
| `filters` | `FilterQuery` + `FilterContext` + `build_filter_context` — **một** ngữ cảnh lọc cho mọi KPI/biểu đồ; viewer bị ép `owner = chính mình`, admin xem tất cả |
| `feed_consumer` | mỗi vòng: lấy `head_revision` từ `canonical/summary`, kéo mọi kind tới cùng head, áp **một transaction** (`processed_events`, `feed_cursor`, seal); cuối vòng so head nên mất event vẫn đuổi kịp; lỗi DB ≠ dead-letter, chỉ event "độc" vào `dead_letters` |
| `readmodels` | `canonical`, `repository`, `apply`, `dataset`, `aggregate`, `channels`, `query` |
| `kpi`, `traffic`, `breakdown`, `country_comparison`, `groups`, `country_config`, `freshness`, `cache` | các endpoint tương ứng |

**Bảng:** `read_resources, read_content, read_daily_metrics, read_events, read_projects, agg_daily (chưa dùng), channel_groups, channel_group_members, country_aliases, country_canonicals, feed_cursor, processed_events, dead_letters, sync_state, account_projection`. Migration `0001_init` · `0002_read_model_keys` (khóa chính khớp khóa tự nhiên của Integration: thêm `resource_type`, events thêm khóa resource; đặt lại con trỏ `canonical.*` để kéo lại) · `0003_processed_events_subject`.

**Endpoint:** `/v1/analytics/{summary,traffic,top-channels,views-by-country,channel-breakdown,country-tags,platform-tags,metrics,freshness,compare/{dimension},groups[...],admin/{rebuild,reconcile,sync,dead-letters[/replay],cache/flush,country-aliases,country-canonicals}}`;
alias legacy `/v1/viewer/*` và `/v1/admin/country-*`, `/v1/admin/cache/flush`. Mọi response dữ liệu có `filter` + `freshness`; header `X-Cache: HIT|MISS|OFF`.
Lỗi: envelope ở `/v1/analytics/*`, `{"detail":…}` ở `/v1/viewer/*` và `/v1/admin/*`.

**Event nhận:** `snapshot.committed`, `resource.changed`, `account.registered|status_changed|role_changed|profile_updated|deleted`. Không phát event.

**Điểm tinh tế:**
- **Seal ở PostgreSQL** (`sync_state('seal')`) tăng **trong cùng transaction** với thay đổi số liệu (apply revision, sửa nhóm/alias, flush); mỗi request đọc seal từ PG trước khi tra cache ⇒ Redis restart không làm sống lại số cũ.
- **Khóa cache:** `analytics:v1:{seal}:{own:<owner>|all}:{endpoint}:{sha256(filter)}` — owner nằm ở scope, không ở hash (bỏ scope sẽ lộ dữ liệu; có test bắt).
- Quy tắc theo *hình dạng dữ liệu*, không hardcode tên nền tảng: bài đăng cộng vào ngày đăng; số liệu theo ngày cộng vào đúng ngày; kênh chưa có resource vẫn được tính khi bộ lọc chỉ gồm owner/nền tảng/"Khác".
- Biểu đồ so sánh quốc gia (`views-by-country`, `country-tags`, `compare/country`) **không** bị thu hẹp bởi `country`; response có `ignored_filters:["country"]`. Hai endpoint danh mục (`country-tags`, `platform-tags`) cố ý nhận tham số thu hẹp.
- `rebuild` kéo hết trước rồi thay thế nguyên tử; nguồn chết thì không đổi gì.
- Không seed alias quốc gia mặc định: admin phải nhập hoặc di chuyển từ DB cũ.
- **Nhóm kênh khoá theo (owner của nhóm, `channel_ref`)**: nhóm/override quốc gia của owner A không tác động kênh cùng `external_id` của owner B khi admin xem toàn bộ. Ngoại lệ có chủ đích: nhóm do admin/dash_admin tạo (role theo `account_projection`) áp xuyên owner. Giới hạn đã biết: dataset gộp hai `resource_type` cùng `(owner, platform, external_id)` thành MỘT kênh khi hiển thị (kho `read_resources` giữ cả hai); `POST /groups/{id}/members` không kiểm kênh thuộc người gọi (thành viên không thuộc owner của nhóm là vô hiệu, không rò rỉ).
- Khoảng ngày: chỉ `YYYY-MM-DD` năm 2000-2100; đảo, quá 401 ngày ⇒ 422 (không còn 200 toàn số 0).
- Event feed hỏng (thiếu `type`/`seq` nguyên) ⇒ dead-letter, không làm kẹt consumer; replay dead-letter của Identity không ghi đè cột đã có event mới hơn (so `seq` theo `(account, cột)`, cần `processed_events.subject`).
---

## 3.4 api-gateway (`:8400`, không DB, package `api_gateway`)

Pure ASGI reverse proxy (httpx, stream cả request lẫn response). **Không** verify JWT, không DB/Redis; `Authorization` đi nguyên xi tới service.

**Env (`GATEWAY_`, `API_GATEWAY_` còn nhận cho `PORT`, `LOG_LEVEL`):** `ENV`, `CORS_ORIGINS` (allowlist; `*`, `null`, path ⇒ lỗi cấu hình), `CORS_ALLOW_CREDENTIALS`, `MAX_BODY_BYTES` (2 MiB), `MAX_HEADER_BYTES`, `MAX_HEADER_COUNT`, `MAX_URL_BYTES`,
`UPSTREAM_{IDENTITY,INTEGRATION,ANALYTICS,REWARD}` (mặc định theo cổng registry), `CONNECT_TIMEOUT_S` (2), `READ_TIMEOUT_S`, `BODY_TIMEOUT_S`, `BREAKER_THRESHOLD`, `BREAKER_COOLDOWN_S`,
`RATE_LIMIT_ENABLED`, `RATE_LIMIT_{STRICT,AUTH,DEFAULT}_PER_MIN`, `TRUSTED_PROXIES`, `ROUTE_MAP_PATH`, `REGISTRY_PATH`.

- `routing/` (`table` longest-prefix tại ranh giới segment, `proxy`, `pathnorm`, `breaker`), `edge_security/` (`headers` HSTS chỉ production/https, `cors`, `limits` 413/408/431/414/405 + cấm Content-Length xung đột, `ratelimit` token bucket theo IP+nhóm route), `access_log/` (`redact` che query/Authorization).
- Chặn `/internal` mọi biến thể (segment `internal` ở bất kỳ vị trí nào ⇒ 404). Strip `X-Service-Key`, `X-Real-IP`, `X-Forwarded-*`, `Forwarded`, `X-Internal-*`, method override, hop-by-hop; đặt `X-Real-IP` từ TCP peer (hoặc từ header nếu peer ∈ `TRUSTED_PROXIES`).
- Upstream lỗi ⇒ `502/504` envelope `UNAVAILABLE`; circuit breaker mỗi upstream ⇒ `503 + Retry-After` (chỉ đếm lỗi transport và 502/504; **timeout đọc ở route `/v1/admin/*` không tính**, để một thao tác admin chậm như `rebuild` không làm sập Dashboard). Log truy cập che invite key `XXXX-XXXX` và mọi segment sau `/v1/admin/keys/`.
- `/v1/health` do gateway tự trả; `/health/ready` chỉ kiểm cấu hình (không gọi upstream); `/health/upstreams` chỉ từ loopback không có header forwarding.
- Sinh nginx: `python infra/gateway/render_nginx.py [--check|--nginx-test]` — nginx là lớp TLS/biên ngoài ở production; test freshness so `nginx.conf` với route-map + registry (**đổi prefix ở registry ⇒ chạy lại render**).
- Chạy tay phải thêm `--no-proxy-headers --no-server-header --no-access-log` (uvicorn access log ghi cả query ⇒ rò token).

---

## 3.5 reward-service (`:8422`, `reward_db`, thư mục `services/reward-service`, package `reward_service`)

Engine phân phối điểm thưởng qua Discord. **Chạy `--workers 1`** (engine giữ trạng thái job trong bộ nhớ).
Skeleton chuẩn như mọi service: mã ở `src/reward_service/{api,domain,engine,discord,identity_link,store}` + `main.py/settings.py/security.py/crypto.py/sdk_path.py`, migration ở `migrations/` (001→005; `005` thêm `processed_events.subject_key`), `Dockerfile`, `README.md`. Chạy: `PYTHONPATH=src python -m uvicorn reward_service.main:app --workers 1`; migrate tay: `python -m reward_service.store.migrate`.

**Env (`REWARD_`):** `REWARD_DATABASE_URL` (**bắt buộc**, cố ý KHÔNG fallback `DATABASE_URL` cũ), `REWARD_IDENTITY_URL`, `REWARD_IDENTITY_JWKS_URL`, `REWARD_ISSUER`, `REWARD_AUDIENCE`, `REWARD_IDENTITY_FEED_URL/KEY`,
`REWARD_TOKEN_KEY` (Fernet — mã hóa token Discord; mất = mất credential), `REWARD_DB_MIGRATE_ON_BOOT`, `REWARD_MAX_CONCURRENT_JOBS`, `REWARD_SDK_PATH` (xem dưới), `REWARD_CORS_ORIGINS`, và nhóm `REWARD_*` timeout/Discord (`.env.example`). `JWT_SECRET` nếu còn trong env bị **bỏ qua**.

- `security.py` dùng `manager_sdk.TokenVerifier` (RS256, JWKS offline). `reward:access` để dùng, `reward:admin` (hoặc role admin) cho route admin; `dash_admin` không đủ.
- `identity_link/` (`client.py` gọi API Identity với timeout ngắn + retry/backoff giới hạn; `consumer.py` feed → `account_projection`/`account_denylist`; `access.py`). Replay dead-letter chỉ bỏ qua event khi có event MỚI HƠN **cùng khóa đặc trưng** (`account|permission`, `account|sid`; revoke toàn account thay thế mọi sid cũ hơn) — không so riêng theo account. SSE job kiểm lại denylist mỗi 3 s và đóng stream khi bị thu quyền/idle. Thu `reward:access`/khóa/xóa/`session.revoked` ⇒ denylist + `WorkerManager.pause_account_jobs`; engine còn kiểm denylist mỗi vòng.
- `api/` (`admin.py`, `auth_proxy.py` — proxy mỏng `login/refresh/logout/me` tới `/v1/auth/*`, đánh dấu deprecated; `credentials.py`; `jobs.py`), `engine/` (`worker_manager`, `job_runner`, `reconcile`, `recovery`, `token_pool`, `confirmation`, `executor_slash`), `discord/`, `domain/`, `store/` (migrations `001→004`, `repositories/`).
- Bảng: `reward_*` (+ `account_projection, account_denylist, feed_cursor, processed_events, dead_letters`). `reward_role_audit` ghi thêm `outcome` và `identity_status`.
- Identity chết ⇒ grant/revoke/list/proxy trả `503 UNAVAILABLE + Retry-After`; request mang token còn hạn **vẫn được phục vụ** nếu JWKS đã cache (khởi động lạnh khi Identity chết ⇒ 401 — xem [07](07-van-hanh-va-lo-trinh.md)).
- `manager_sdk` không phải gói PyPI: bản deploy phải mang theo (`REWARD_SDK_PATH` hoặc `vendor/`) — `sdk_path.py`, `docs/operations/legacy-vps-deploy.md`.
- Hợp đồng thay đổi so với trước (web đã theo): `/admin/accounts` có `reward_access` (UI dựa vào đây thay vì `role==='discord'`); `revoke-discord` 409 nếu quyền chỉ là quyền ngầm của role; `health` bỏ `jwt_secret_fingerprint`, thêm `feed{enabled,cursor,last_error}`.

---

## 3.6 manager-web (`apps/manager-web/`)

React + Vite + TypeScript. Dev: `npm run dev` (bind `127.0.0.1:5174`, proxy `/v1`, `/oauth`, `/.well-known` → gateway `:8400`). Cổng chất lượng: `npm run typecheck`, `npm test` (vitest), `npm run build` (+ `postbuild` quét bundle).

- `src/lib/http.ts` (Bearer, refresh 401 gộp một lần, timeout, AbortController, bóc envelope) · `src/lib/errors.ts` (hiểu cả envelope mới và `{detail}` legacy) · `src/api.ts` (`analyticsApi, authApi, adminApi, permissionsApi, integrationApi, accountsApi`) · `src/reward/api.ts`.
- `src/filters/` (`filterState.ts` thuần 8 tham số, `FilterContext.tsx` nguồn sự thật duy nhất đồng bộ URL, `useAnalyticsQuery.ts` hủy request cũ/giữ dữ liệu cuối khi 503, `freshness.ts`, `compareDimensions.ts`).
- `src/auth/` (`tokenStore.ts`, `oidc.ts` PKCE S256 + state + nonce, `permissions.ts`), `src/auth.tsx`; `VITE_AUTH_MODE=oidc` bật SSO.
- `routes/`: Dashboard, Admin (invite key + `PermissionsDialog`), `IntegrationClientsPage` (`/integrations`), Register (đăng ký kèm Tool), Groups, Resources, Profile, Reset/Verify, `OidcCallbackPage`. `components/SecretOnceDialog` (API key hiện đúng một lần, không vào storage).
- **Giao diện v1** (`docs/architecture/ui-design-v1.md`): thanh bên theo việc + thanh tab/ngăn kéo trên điện thoại (`src/nav.ts`, `Layout.tsx`), bộ kit dùng chung `src/components/kit.tsx`, token ở `src/index.css`, chữ Be Vietnam Pro tự host, nền 15px (người dùng chọn cỡ chữ chuẩn/lớn/rất lớn và sáng/tối/theo máy ở `lib/preferences.ts` — ngoại lệ DUY NHẤT được dùng `localStorage`, có test khóa). Trang mới phải dùng kit và theo quy tắc viết/vai trò ở tài liệu đó.
- Quy tắc cứng: không `dangerouslySetInnerHTML/innerHTML` (test quét nguồn); refresh token **không** vào `localStorage`; quét bundle không được có secret.

---

## 3.7 Packages dùng chung (chỉ generic)

| Package | Nội dung |
|---|---|
| `packages/contracts` | `manager_contracts`: `ErrorCode` (mã ổn định — **thêm mã được, đổi nghĩa/xóa = breaking**), `ApiError`, envelope; `ingest/2026-10-01/` JSON Schema + ví dụ |
| `packages/observability` | `manager_observability`: `app_factory` (`error_style` envelope|legacy), health (timeout từng check 1.5s), request-id, log JSON (gồm logger uvicorn) |
| `packages/sdk-python` | `manager_sdk`: `TokenVerifier` (JWKS cache, stale-while-error, min_refetch), `Principal`, `fastapi_deps` (`make_dependencies`, `install_auth_error_handler`), `ServiceKeyAuth`, `FeedClient` + `backoff_delays`, `IntegrationClient` (SDK cho Tool) |

`IntegrationClient`: `Idempotency-Key = sha256(thao tác + nội dung)` nên retry gửi lại **đúng khóa + đúng body**; chỉ retry lỗi mạng/5xx/429 (tôn trọng `Retry-After`); 4xx ném `IntegrationError(code)`;
tự chia lô ≤500, tự tính `expected_counts`; `push_snapshot` = tạo → tải → commit, lỗi giữa chừng thì **không commit**; không bao giờ đưa API key vào exception/log. `claim_jobs` cố ý **không** có Idempotency-Key.
