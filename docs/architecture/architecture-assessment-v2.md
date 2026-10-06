# Architecture Assessment v2 — Manager Platform

Trạng thái: **ĐỀ XUẤT, CHỜ DUYỆT**. Chưa có thay đổi source code nào.
Thay thế bản v1 (chỉ nằm trong hội thoại). Nguồn yêu cầu: prompt kiến trúc +
`tasks/backlog/system-architecture-microservices-v1.md` +
`tasks/backlog/api-first-integration-contract-v1.md`.

---

## 0. Những gì v2 sửa so với v1

| # | Vấn đề ở v1 | Cách v2 xử lý | Mục |
|---|---|---|---|
| F1 | Analytics đọc cross-DB bảng `documents`; Tool ghi thẳng DB | `documents` vào schema Integration sở hữu, sau **Anti-Corruption Layer**; Analytics chỉ nhận **change feed**. Không còn đọc cross-DB | 2.1 |
| F2 | Dual-run Identity có nguy cơ split-brain | **Single-writer cutover** có freeze, shadow replication một chiều, rollback có journal | 2.2 |
| F3 | Đăng ký kèm Tool: lỗi chỉ phát hiện async | Validate **đồng bộ, cục bộ** theo policy trong registration key; provisioning có trạng thái và failure event | 2.3 |
| F4 | Chuyển permission `discord` sang Reward, lệch yêu cầu | **Identity giữ role + permission**. Reward chỉ khai báo catalog quyền và enforce cục bộ | 2.4 |
| F5 | OIDC + PKCE không có trong kế hoạch | Thêm workstream **Phase 1c** | 2.5 |
| F6 | Chưa audit Dashboard / Filter Context / so sánh quốc gia | Thêm **Dashboard Conformance Inventory** ở Phase 0 và việc manager-web ở Phase 3 | 2.6 |
| F7 | Thiếu route map và quy tắc versioning | Bảng ánh xạ + **header contract-version, mặc định legacy** | 2.7 |
| F8 | Thiếu nền tảng vận hành từng service | **Service Chassis Contract** + test conformance | 2.8 |
| F9 | Phase 0 bị mở rộng (gateway) | Phase 0 chỉ freeze. Gateway thành bước riêng, cần duyệt | 4 |
| F10 | Redis Streams coi là có sẵn | Độ bền nằm ở **outbox trong DB**. Redis chỉ là đường tăng tốc tùy chọn | 2.1, 3.3 |
| F11 | Khẳng định nginx chưa kiểm chứng | Đưa vào inventory Phase 0 (lấy cấu hình thật từ VPS, chỉ đọc) | 4 |
| F13 | Redis chưa được đặt vào kiến trúc đích | **Cache-Aside + Invalidation** theo ranh giới service: cache thuần, ACL và prefix riêng, `DEL` hoặc seal phiên bản sau commit, Redis chết thì rơi về PostgreSQL | 2.10 |
| F12 | Website thứ hai dùng lại đăng ký/xác thực của Manager chưa được thiết kế; thu hồi quyền chưa tới được website ngoài | Mô hình **Relying Party**: đăng ký OIDC client, `allowed_apps` trên key, thu hồi 4 lớp (verify offline → TTL ngắn → refresh → back-channel/introspection) | 2.9 |

---

## 1. Cấu trúc chuẩn của Integration Plane

Một hệ thống tích hợp đúng chuẩn tách rõ các tầng. Mỗi tầng chỉ biết tầng kế bên qua contract.

```text
┌──────────────────────────── EXTERNAL ────────────────────────────┐
│  Tool A / Tool X (machine)      Fallback Tool (machine, system)  │
│  Operator UI / Desktop (user, OIDC+PKCE)                         │
└───────────────┬───────────────────────────────┬──────────────────┘
                ▼                               ▼
L1  EDGE        Gateway: TLS · request-id · rate limit · CORS · route (không business logic)
L2  PRINCIPAL   credential ─► Principal{account, client, installation, scopes} ─► owner
                (owner LUÔN suy ra từ credential, bỏ qua mọi owner_* trong payload)
L3  CONTRACT    OpenAPI theo contract_version · capability negotiation · error.code ổn định
                · Idempotency-Key · envelope {ok,data,meta}
L4  INGRESS     receive ─► validate ─► canonicalize ─► stage ─► commit(atomic) ─► publish
L5  CANONICAL   Platform Registry · Metric Registry · Resource · Project binding · Snapshot
L6  OUTBOUND    Transactional outbox ─► change feed (pull) ─► stream (push, tùy chọn)
L7  CONTROL     Client lifecycle · credential rotate/revoke · policy · audit
L8  ACL         Legacy adapter (anti-corruption): dịch payload/DB cũ ─► canonical
L9  OBSERVE     log JSON · request_id · trace_id · client_id · latency · error.code
```

Các thành phần bắt buộc của L2–L6:

- **Principal model:** `Account 1─n Client 1─n Installation`, `Client 1─n Credential`.
  Credential lưu hash, có `key_id` để audit, rotate, revoke, `last_used_at`, `expires_at` tùy chọn.
- **Platform Registry / Metric Registry là dữ liệu, không phải code.** Thêm platform hay metric chính thức
  là thêm bản ghi có version. Metric riêng của Tool đi vào `extensions`, chưa vào KPI.
- **Idempotency store:** khóa `(client_id, route, Idempotency-Key)` + hash body.
  Trùng khóa, cùng body: trả lại response đã lưu. Trùng khóa, khác body: `IDEMPOTENCY_CONFLICT`.
- **Snapshot state machine:** `building → committing → committed | failed | abandoned`.
  Current pointer theo `(owner, platform, project, snapshot_type)` chỉ đổi bằng một transaction ở bước commit.
  Reaper đánh dấu `abandoned` snapshot `building` quá TTL. Snapshot cũ hơn `observed_at` của current bị từ chối.
- **Event envelope** (kiểu CloudEvents): `id, type, source, time, subject, data_version, data`.
  Giao hàng at-least-once, consumer idempotent theo `id`.
- **Capability negotiation:** dùng `declared ∩ allowed`.

---

## 2. Xử lý từng lỗ hổng

### 2.1 F1 + F10 — Legacy ingest và pipeline dữ liệu

**Sự thật hiện tại:** sync server của Tool (repo `upload-facebook`) ghi `documents`/`user_meta` vào Postgres
`ufsync` qua `POST /v1/push`. Admin-server parse JSON thô tại `db.py:1729,1748`.

**Quyết định cấu trúc:**

1. `documents` và `user_meta` chuyển vào **schema `legacy_ingest` do Integration sở hữu**.
2. DB credential của sync server bị thu hẹp: chỉ `INSERT/UPDATE` trên `legacy_ingest`, không thấy gì khác.
   Đây là **cổng ghi hẹp có hạn dùng cho một client bên ngoài**, không phải cross-service access.
3. Integration chạy **Legacy Canonicalizer** (L8): `documents → canonical snapshot (committed) → outbox`.
4. Analytics **không bao giờ đọc `documents`**. Nó chỉ nhận event/feed. Không còn "legacy mode" trong Analytics.
5. Lộ trình L2 (khi Tool sẵn sàng): sync server chuyển sang `POST /v1/integrations/*`, xóa `legacy_ingest`.

**Change feed (thay Redis làm nền):**

```text
GET /v1/integrations/internal/feed?after=<cursor>&limit=N     (service-to-service, scope feed:read)
→ { events:[{id,type,subject,data_version,data}], next_cursor }
```

- Nguồn là bảng `outbox` của Integration, độ bền do Postgres đảm bảo.
- Analytics poll feed từ Phase 3. Phase 5 thêm push (Redis Streams) với **cùng schema event**.
  Redis chết thì Analytics vẫn kéo được.
- Analytics lưu `last_cursor` + `processed_events`. Mất read model thì replay từ cursor 0 (backfill).

**Rủi ro mới cần kiểm chứng ở Phase 0:** canonical model phải đủ nuôi mọi thứ `stats_core` đang dùng
(`fb_posts`, `yt_daily`, channel meta, events, pairings, group, country override). Phase 0 phải lập
**Dashboard Data Requirement Inventory** (xem 2.6). Metric Registry mở rộng theo inventory, không đoán.

### 2.2 F2 — Cutover Identity không split-brain

Nguyên tắc: tại mọi thời điểm **đúng một writer** cho `accounts`, `invite_keys`, `refresh_tokens`.

| Bước | Writer | Hành động |
|---|---|---|
| A. Shadow | `admin-server` (ufsync) | Replicate **một chiều** ufsync → `identity_db` (logical replication hoặc sync định kỳ). identity-service chạy read-only, đối chiếu parity |
| B. Freeze | tạm không ai | Ghi auth trả `503 + Retry-After` vài giây. Chốt LSN/checksum |
| C. Flip | `identity-service` | Đổi route ở gateway + cờ `IDENTITY_MODE=proxy` trong admin-server (auth/admin keys/users thành proxy mỏng). `REVOKE UPDATE` trên `ufsync.accounts*` |
| D. Rollback window | `identity-service` | Mọi ghi mới vào `identity_db` có **journal** (outbox) để replay ngược nếu phải quay về |
| E. Contract | `identity-service` | Sau cửa sổ ổn định: xóa bảng cũ ở `ufsync` |

- `refresh_tokens` được copy cùng để phiên hiện có không bị đăng xuất.
- Đổi HS256 → RS256: cửa sổ chấp nhận cả hai ≤ TTL access token (15 phút). Sau đó tắt HS256.
- Điều kiện flip: parity test xanh, checksum khớp, rollback đã diễn tập trên staging.

### 2.3 F3 — Đăng ký kèm Tool, validate đúng chỗ

Nguyên tắc: **register không phụ thuộc Integration lúc runtime** và **lỗi không được im lặng**.

1. Admin tạo key: `integration_policy` (allowed_platforms, allowed_scopes, max_clients, contract_version).
   Lúc **admin tạo key**, Identity gọi Platform Registry của Integration (có cache) để kiểm tra platform/scope
   có tồn tại. Đây là thao tác admin, không nằm trong đường đăng ký của user.
2. User đăng ký: Identity validate **cục bộ** `requested ⊆ policy`, `allow_integration_registration`, `max_clients`.
   Sai thì trả lỗi đồng bộ `PLATFORM_NOT_ALLOWED` / `SCOPE_DENIED`, **không tạo account**.
3. Hợp lệ: ghi `account` + `provisioning_request` + outbox trong **một transaction**.
   Response: `client_id` (ULID Identity cấp sẵn), `provisioning: "pending"`.
4. Integration consume `account.registered`, tạo Client, binding, trạng thái `pending_email_verification`.
   Revalidate phòng thủ. Nếu vẫn lỗi: emit `integration.client.provision_failed`.
5. Identity ghi nhận trạng thái. User xem qua `GET /v1/accounts/me/integration`
   (`pending | active | failed + error.code`).
6. Cấp credential: `POST /v1/integrations/{client_id}/credentials` chỉ khi
   claim `email_verified` trong user token và Client thuộc `sub`.

### 2.4 F4 — Role và permission thuộc Identity

Đúng yêu cầu: Identity là nguồn sự thật cho **user role và permission**.

- Role (thô): `viewer`, `dash_admin`, `admin`.
- Permission (mịn, theo namespace): `reward:access`, `reward:admin`, … Catalog do từng domain **khai báo**
  trong `packages/contracts` (dữ liệu), Identity lưu grant.
- Cấp/thu quyền qua Identity: `PUT/DELETE /v1/admin/accounts/{id}/permissions/{perm}`.
  Các luật hiện nằm trong SQL của Reward (`role<>'admin' AND status='active'`) chuyển về Identity.
- Token mang claim `perms`. Reward enforce cục bộ, không gọi Identity trên đường request.
- Reward consume `account.permission_changed`: pause job đang chạy khi bị thu quyền
  (thay cho transaction ghi chéo hiện tại). Có denylist cục bộ để hiệu lực nhanh hơn TTL token.
- `reward_role_audit` thành audit của chính Reward, nuôi bằng event. Audit gốc nằm ở Identity.
- **Shim tương thích:** Identity phát thêm claim `role="discord"` khi có `reward:access`, cho FE cũ. Bỏ ở Phase 6.

### 2.5 F5 — OIDC + PKCE (Phase 1c)

Identity bổ sung:

- Discovery `/.well-known/openid-configuration`, JWKS (`kid`, xoay khóa), `/authorize`, `/token`
  (authorization_code + PKCE S256, refresh), `/userinfo`.
- Client registry cho **public client** first-party: manager-web, desktop, operator UI.
  Website ngoài (Relying Party) đăng ký theo mục 2.9.
- API đăng nhập password hiện tại giữ nguyên để tương thích.
- manager-web chuyển sang PKCE **sau**, bật bằng cờ, không bắt buộc cho đến Phase 6.
- **Machine Tool không dùng OIDC.** Chỉ dùng `client_id + API key + scopes`.
- Tool B tách hai mặt phẳng: operator đăng nhập bằng PKCE (user plane), còn thao tác máy bằng credential (machine plane).
  Hiện `local-ops-hub` đăng nhập bằng password admin qua `/v1/auth/login`. Phải gỡ.
- Test C: một Integration App mẫu chạy PKCE + `/userinfo`, không có user DB riêng.

### 2.6 F6 — Dashboard giữ nghiệp vụ, Filter Context thống nhất

Phase 0 tạo **Dashboard Conformance Inventory** (chưa kết luận gì khi chưa audit):

| Cột | Nội dung |
|---|---|
| Widget | từng KPI và biểu đồ trong `DashboardPage.tsx` |
| Endpoint hiện tại | `/v1/viewer/*` |
| Endpoint đích | `/v1/analytics/*` |
| Read model | bảng/aggregate cần có |
| Filter dùng | `date_from, date_to, owner, platform, country, project, group` |
| Cờ yêu cầu | so sánh quốc gia nằm trong biểu đồ riêng; biểu đồ chung không render lại |

Điểm cần audit: `DashboardPage.tsx:435` có `CountryComparisonCard`, còn khối "Biểu đồ chung" (dòng ~917)
ghi chú là so sánh cả quốc gia. Có thể đang lệch yêu cầu. Chưa kết luận.

Phase 3 thêm cho **manager-web**: một `FilterContext` provider duy nhất, mọi widget lấy cùng
`FilterContext` và gửi cùng một tham số chuẩn. Backend: một schema `FilterContext` dùng cho mọi endpoint
`/v1/analytics/*`. Có `freshness` (`as_of`, `snapshot_id`) và banner stale khi feed trễ.

### 2.7 F7 — Route map và versioning

**Quy tắc negotiation:** path giữ `/v1`. Hợp đồng chọn bằng header `X-Contract-Version`.
Thiếu header = contract **`legacy`** (đóng băng, giữ đúng shape cũ, có characterization test).
Có header ≥ `2026-10-01` = envelope `{ok,data,meta}` mới. Response legacy có `Deprecation` + `Sunset`.
Không đổi payload cũ âm thầm.

| Legacy (hiện tại) | Đích | Ghi chú |
|---|---|---|
| `/v1/auth/*` (validate-key, check-*, register, verify-email, resend-verify, login, refresh, logout, me, forgot/reset, change-password, profile) | identity `/v1/auth/*` | cùng path, negotiation bằng header |
| `/v1/admin/keys*`, `/v1/admin/users*` | identity `/v1/admin/accounts/*` | alias legacy giữ ở gateway |
| `/v1/admin/cache/flush`, `country-canonicals*`, `country-aliases*` | analytics `/v1/analytics/admin/*` | |
| `/v1/viewer/{summary,traffic,top-channels,views-by-country,country-tags,platform-tags,metrics,channel-breakdown}` | analytics `/v1/analytics/*` | |
| `/v1/viewer/groups*` | analytics `/v1/analytics/groups*` | cấu hình dashboard |
| `/v1/viewer/links*` | integration `/v1/integrations/legacy/links*` | deprecated, chỉ cho Tool cũ |
| `/v1/resources/*` | integration `/v1/resources/*` | |
| `/v1/ops/{inventory,users,sources,platforms,platform-policies,resources/*/execution-mode}` | integration `/v1/resources/admin/*` | |
| `/v1/ops/batches*`, `/v1/ops/items*` | integration `/v1/integrations/jobs*` (phía admin) | |
| `/v1/reward/*` | reward `/v1/rewards/*` | alias legacy giữ |

**Cần xác minh ở Phase 0:** `infra/deploy/legacy/setup-vps.sh` chỉ có 4 location (`/admin/`, `/v1/auth/`, `/v1/viewer/`,
`/v1/admin-api/`), trong khi FE gọi `/v1/admin/...`, `/v1/ops`, `/v1/reward`. Có thể cấu hình thật khác repo.
Phải lấy nginx thật từ VPS (chỉ đọc) trước khi dựng gateway.

### 2.8 F8 — Service Chassis Contract

Mỗi service bắt buộc có, và CI kiểm tra bằng `tests/arch/test_service_chassis.py`:

| Hạng mục | Yêu cầu |
|---|---|
| Health | `GET /health/live`, `GET /health/ready` (ready kiểm DB của chính nó, **không** kiểm service khác) |
| Contract | `/openapi.json` theo version, request/response schema, error.code, scopes, idempotency, examples |
| Config | `.env.example` + schema validate lúc boot. Thiếu config thì fail-fast. Dependency chết thì degrade, không crash |
| Migration | thư mục migration riêng, chạy như job riêng, không chạy ngầm lúc boot của service khác |
| Logging | JSON qua `packages/observability`: `service_name, service_version, request_id, trace_id, client_id, error_code` |
| Build | Dockerfile riêng, artifact riêng, graceful shutdown |
| CI | pipeline path-filtered, test riêng |
| Outbound | timeout bắt buộc, retry có backoff, circuit breaker, không retry vô hạn |
| DB | credential riêng, chỉ có quyền trên DB/schema của mình |

**Scope catalog (machine) và ai được cấp:**

| Scope | Mô tả | Cấp bởi |
|---|---|---|
| `integration:handshake`, `resources:write`, `snapshots:write`, `metrics:write` | Tool thường, trong owner của mình | registration key policy |
| `jobs:claim`, `jobs:complete` | claim job của chính owner | policy |
| `jobs:claim:system` | claim job **đa owner** (Fallback Tool), owner do server cấp | chỉ admin, chỉ `client_kind=fallback_tool` |
| `analytics:read` | đọc analytics bằng machine | admin |
| `feed:read` | đọc change feed nội bộ | chỉ service-to-service |

### 2.9 F12 — Website khác dùng lại đăng ký và xác thực của Manager

**Vai trò:** Identity là nhà cung cấp danh tính (IdP). Website mới là **Relying Party (RP)**, đăng ký như
một OIDC client. RP không giữ mật khẩu và không có bảng user riêng. Nó chỉ lưu `sub` làm khóa tham chiếu.

```text
Người dùng ─► Website B ─redirect─► Identity (đăng ký / đăng nhập / verify email)
                 ▲                        │
                 └──── code + PKCE ◄──────┘
Backend B ──► Identity /token (đổi code, xác thực client)
Backend B ◄── JWKS · introspect · back-channel logout ◄── Identity
Backend B ──► Gateway ──► Integration / Analytics        (mặt phẳng máy: API key + scopes)
```

**a) Đăng ký website (RP) trong Identity** — bảng `oidc_clients`:

| Trường | Ý nghĩa |
|---|---|
| `client_id`, loại | `public` (SPA, PKCE) hoặc `confidential` (backend, secret + PKCE) |
| `redirect_uris` | so khớp **chính xác**, không wildcard |
| `audience` | giá trị `aud` trong access token cho website này |
| `allowed_scopes` | `openid profile email` + scope riêng của app |
| `backchannel_logout_uri` | nơi Identity gửi thông báo thu hồi |
| `status`, TTL token | bật/tắt client, ghi đè TTL nếu cần |

**b) Quyền đăng ký theo app:** registration key có thêm `allowed_apps`. Key phát cho app nào chỉ cấp
permission `<app>:access` cho app đó. Người có key của website A không tự vào được website B.
Trang đăng ký và verify email do Identity phục vụ, nhận `client_id` để giữ giao diện và redirect về đúng website.
Tài khoản dùng chung toàn hệ thống (SSO). Quyền vào từng website là permission riêng cấp qua Identity.

**c) Kiểm tra "tài khoản còn hiệu lực" — 4 lớp:**

| Lớp | Cơ chế | Độ trễ tối đa khi bị khóa |
|---|---|---|
| 1. Verify cục bộ | chữ ký qua JWKS (`kid`), `iss`, `aud` đúng app, `exp`/`nbf` (leeway nhỏ), chỉ RS256/EdDSA, từ chối `none` và HS256 | tới khi token hết hạn |
| 2. Token ngắn hạn | access token **10 phút** (mặc định) | ≤ TTL |
| 3. Refresh là điểm kiểm tra | Identity kiểm `status` tài khoản + session còn sống; refresh token xoay vòng, phát hiện dùng lại thì thu hồi cả chuỗi | ≤ TTL |
| 4. Thu hồi tức thì | **Back-channel logout** (OIDC): Identity POST `logout_token` đã ký tới RP, RP đưa `sub`/`sid` vào denylist đến hết hạn token. **Introspection** `POST /oauth/introspect` (RFC 7662) cho thao tác nhạy cảm | vài giây |

Chính sách khi Identity không trả lời:
- Thao tác thường: **verify offline**, tiếp tục chạy tới khi token hết hạn.
- Thao tác nhạy cảm (thanh toán, đổi quyền, hành động admin, xóa dữ liệu): bắt buộc introspection, **fail-closed**.
- Danh sách thao tác nhạy cảm do từng RP khai báo và được test (xem D13).

**d) Hai mặt phẳng giao tiếp, không lẫn nhau:**

| Mặt phẳng | Chủ thể | Credential | Dùng để |
|---|---|---|---|
| Người dùng | browser + backend B | OIDC code + PKCE, token `aud=B` | biết người dùng là ai |
| Máy | service của B | `client_id + API key + scopes` do Integration cấp | đẩy dữ liệu, đọc analytics |

Token người dùng không dùng làm credential máy và ngược lại. Mọi lời gọi vào Manager đi qua Gateway.
Service của B không bao giờ chạm `identity_db`.

**e) Điều kiện tiên quyết:** RP **không được nhận `JWT_SECRET`**. Secret HS256 dùng chung hiện tại
(`auth.py:33`) cho phép bên giữ nó tự ký token của bất kỳ ai. Do đó bước 1.4 (RS256 + JWKS) phải xong
trước khi onboard website thứ hai.

**f) Bảo mật bắt buộc:** chỉ Authorization Code + PKCE S256 (không implicit); kiểm `state` và `nonce`;
RP có backend dùng mẫu BFF, token nằm trong cookie HttpOnly; xoay khóa ký giữ khóa cũ theo `kid`, RP làm mới
JWKS khi gặp `kid` lạ (có giới hạn tần suất); yêu cầu `email_verified` trước khi cấp `<app>:access`;
xóa tài khoản phát `account.deleted` và back-channel logout đến mọi RP.

**g) SDK dùng chung** (`packages/sdk-typescript`, `sdk-python`; chỉ chứa tiện ích generic, không có logic domain):
`verifyAccessToken`, middleware, handler back-channel logout, `introspect`. Mục đích: mỗi website không tự cài sai.

**h) Các bước phát triển website mới (checklist):**

1. Admin đăng ký client trong Identity, nhận `client_id` (và secret nếu confidential).
2. Tạo permission `<app>:access`; phát registration key có `allowed_apps=[<app>]`.
3. Website cài SDK, cấu hình `issuer`, `audience`, `redirect_uri`.
4. Luồng login: redirect `/authorize` → nhận code → backend đổi token → tạo session phía website.
5. Đăng ký endpoint back-channel logout; khai báo danh sách thao tác nhạy cảm.
6. Nếu cần đẩy hoặc đọc dữ liệu Manager: tạo Integration Client riêng, dùng API key.
7. Chạy bộ test conformance của RP (mục 6).

### 2.10 F13 — Redis Cache-Aside + Invalidation

Hiện `legacy/admin-server/cache.py` đã làm Cache-Aside cho 5 endpoint viewer, với seal phiên bản nằm trong khóa
và Redis lỗi thì rơi về PostgreSQL. v2 giữ nguyên các nguyên tắc đó và đặt chúng vào ranh giới service.

**Vị trí:** Redis là **cache thuần**, không phải nguồn sự thật. Outbox, change feed, idempotency key, snapshot
current pointer và mọi dữ liệu nghiệp vụ vẫn nằm trong PostgreSQL. Mất Redis chỉ làm chậm, không mất dữ liệu.

**Luồng ĐỌC:** `GET → Redis`. HIT thì trả ngay. MISS thì đọc PostgreSQL, `SET` kèm TTL, rồi trả.

**Luồng GHI (CREATE / UPDATE / DELETE):** ghi PostgreSQL → transaction OK → **mới** invalidate Redis.
Không xóa trước khi commit, vì một request đọc xen vào sẽ nạp lại dữ liệu cũ.

**Hai kiểu invalidate:**

| Loại khóa | Cách | Lý do |
|---|---|---|
| Entity đơn lẻ (profile, platform, cấu hình quốc gia) | `DEL key` | một khóa, dễ xác định |
| Kết quả tổng hợp theo bộ lọc dashboard | tăng **seal phiên bản** nằm trong khóa (khóa cũ tự không còn với tới được) | số tổ hợp bộ lọc quá lớn để `DEL` từng khóa; hiện thực này đã có trong `cache.py` |

**Ranh giới giữa các service (bắt buộc):**
- Mỗi service có **ACL user riêng và prefix khóa riêng** (`{service}:{schema}:...`). Có thể dùng chung một instance
  vật lý, như chung cluster PostgreSQL, nhưng không service nào đọc hay xóa khóa của service khác.
- Chỉ service **sở hữu dữ liệu** mới invalidate khóa của nó. Cross-service chỉ đi qua event, không `DEL` chéo.
- Giai đoạn đầu chỉ bật cho **analytics** (đọc nặng). Integration (registry, tra credential) và Identity
  (profile) bật sau khi đo thấy cần. Reward chưa cần.
- Analytics có hai đường ghi: thao tác người dùng (nhóm kênh, bí danh quốc gia) và consumer của change feed.
  Cả hai đều phải invalidate sau khi commit.

**Bảo mật của khóa cache:**
- Khóa phải chứa **scope đã phân giải của người gọi** (owner, role) cùng Filter Context. Nếu thiếu, một viewer
  có thể nhận kết quả đã cache của admin. Logic phân giải scope hiện ở `viewer.py` (`_resolve_scope`).
- Không cache secret hay API key. Kết quả tra credential chỉ chứa dữ liệu tối thiểu, **TTL ≤ 60 giây**,
  và bị xóa chủ động khi credential bị thu hồi.
- Không dùng cache cho quyết định cấp quyền nếu chưa có TTL ngắn. Introspection (mục 2.9) đọc nguồn gốc.

**Rủi ro và cách chặn:**

| Rủi ro | Cách chặn |
|---|---|
| Race: reader MISS đọc dữ liệu cũ, writer commit và xóa, reader mới `SET` dữ liệu cũ | Seal phiên bản đọc **trước** khi query và chỉ `SET` nếu seal chưa đổi; luôn có TTL làm chặn trên |
| `DEL` lỗi sau commit | Dữ liệu cũ tồn tại tới hết TTL; retry; không rollback giao dịch vì Redis |
| Stampede khi MISS hàng loạt | Single-flight (đã có), TTL có jitter |
| Redis chết | Mọi thao tác cache nuốt lỗi, rơi về PostgreSQL, có circuit breaker. `/health/ready` **không** phụ thuộc Redis |
| Cache che độ trễ dữ liệu | Response vẫn mang `freshness.as_of` từ read model |

---

## 3. Ma trận cập nhật

### 3.1 Data Ownership

| DB | Sở hữu |
|---|---|
| `identity_db` | `accounts`, `invite_keys` (+`integration_policy`), `refresh_tokens`, `permissions`, `account_permissions`, `oidc_clients`, `signing_keys`, `auth_audit`, `outbox` |
| `integration_db` | `integration_clients`, `integration_credentials`, `installations`, `platform_registry`, `metric_registry`, `projects`, `resources`, `resource_project_bindings`, `snapshots`, `snapshot_items`, `idempotency_keys`, `jobs`, `audit_log`, `account_projection`, `outbox`; **schema `legacy_ingest`**: `documents`, `user_meta`, `account_links`, `link_codes` |
| `analytics_db` | read models, `agg_*`, `channel_groups`, `channel_group_members`, `country_aliases`, `country_canonicals`, `account_projection`, `feed_cursor`, `processed_events` |
| `reward_db` | 12 bảng `reward_*`, `account_denylist`, `account_projection` tối thiểu |

### 3.2 Sync / Async

| Luồng | Kiểu |
|---|---|
| login, register, refresh, profile, OIDC | Sync, web ↔ identity |
| Verify user token | Offline (JWKS cache) |
| Verify API key | Local trong integration |
| Handshake, resource upsert, snapshot create/append/commit, job claim/complete | Sync, Tool ↔ integration |
| `snapshot.committed`, `resource.changed` | Async: outbox → change feed (pull), push tùy chọn |
| `account.registered / verified / status_changed / deleted / permission_changed` | Async: outbox của identity |
| `integration.client.provisioned / provision_failed` | Async: integration → identity |
| Dashboard query | Sync, web ↔ analytics (chỉ đọc `analytics_db`) |
| Tạo key có integration policy | Sync, identity → integration Platform Registry (thao tác admin, có cache, lỗi thì từ chối tạo key) |

Không còn cạnh sync nào trên đường request của người dùng hay Tool giữa hai service.

### 3.3 Hạ tầng sự kiện

Outbox trong DB là nguồn sự thật về độ bền. Feed pull là đường mặc định. Redis Streams là tối ưu hóa
tùy chọn ở Phase 5 (cần bật AOF nếu dùng). Mất Redis không làm mất event.

---

## 4. Kế hoạch migration điều chỉnh

Giữ thứ tự bạn đưa ra. **Có hai điều chỉnh có chủ đích:** (a) bước Reward bỏ `accounts` đi cùng Phase 1;
(b) change feed xuất hiện từ Phase 2 để Analytics không bao giờ có chế độ đọc legacy.

| Phase | Nội dung | Cổng duyệt |
|---|---|---|
| **0 Freeze** | Chỉ tài liệu và kiểm kê: ADR, OpenAPI legacy, route map, **Dashboard Data Requirement Inventory**, **nginx thật từ VPS (chỉ đọc)**, schema `documents` và các `store` đang dùng, danh sách consumer của từng endpoint legacy. Đề xuất commit baseline | Duyệt trước khi commit baseline |
| **1.0** | Gateway pass-through trước 2 process (không đổi hành vi) | **Duyệt riêng** |
| **1.1** | identity-service + chassis + `identity_db` | |
| **1.2** | Shadow replication một chiều + parity | |
| **1.3** | Single-writer cutover (2.2) | Duyệt trước khi flip |
| **1.4** | JWKS/RS256 (dual-accept), permission model, `account.*` + outbox | |
| **1.5** | Reward: verify bằng JWKS, quyền qua Identity, **bỏ `accounts_repo`** | |
| **1.6 (1c)** | OIDC + PKCE: (a) core, (b) registry client cho website ngoài + `allowed_apps` trên key, (c) back-channel logout + introspection, (d) hàm verify trong SDK (mục 2.9) | Duyệt trước khi onboard website thứ hai |
| **1.7** | Registration kèm integration: validate cục bộ + provisioning event (2.3) | |
| **2** | integration-service: principal/credential/handshake → registry, project, resource, idempotency, audit → snapshot state machine + outbox + feed → **Legacy ACL + canonicalizer** (thu hẹp credential sync server) → job queue + system scope, Tool B tách hai mặt phẳng → consumer provisioning → Tool X conformance | Duyệt trước khi thu hẹp credential sync server |
| **3** | analytics-service: consume feed + `account.*`, read models, `FilterContext`, parity shadow với viewer cũ, manager-web dùng SDK + FilterContext, kiểm so sánh quốc gia, flip dashboard | Duyệt trước khi flip |
| **4** | Reward dọn nốt: bỏ `auth_proxy`, kiểm chứng thu hồi quyền DB chéo | |
| **5** | Push transport (tùy chọn), rebuild nền, DLQ, replay tooling | |
| **6** | Chuyển 100% traffic, bỏ shim `discord`, retire admin-server; L2 Tool chuyển sang Integration API, xóa `legacy_ingest` | |

Quy tắc chung: expand → shadow → flip ở gateway → contract. Rollback = lật route về legacy.

---

## 5. Failure Scenarios (bổ sung)

| Sự cố | Hành vi mong đợi |
|---|---|
| Đăng ký với platform ngoài policy | Lỗi đồng bộ `PLATFORM_NOT_ALLOWED`, không tạo account |
| Integration down lúc register | Register vẫn thành công, `provisioning: pending`, event retry. Khi phục hồi thì provisioned |
| Provision thất bại sau khi accepted | Event `provision_failed`, user thấy `failed + error.code`, admin xử lý được |
| Redis chết | Không mất event. Analytics chuyển về poll feed |
| Analytics mất read model | Replay feed từ cursor 0 |
| Flip Identity thất bại | Lật route về legacy. Replay journal để không mất ghi mới |
| Sync server ghi `legacy_ingest` trong lúc Integration down | Ghi vẫn thành công (cổng hẹp), canonicalizer xử lý khi Integration lên |
| Thu quyền `reward:access` | Job đang chạy được pause qua event. Denylist có hiệu lực trước khi token hết hạn |
| Token cũ HS256 sau cửa sổ chuyển | Bị từ chối `AUTH_INVALID` |
| Tài khoản bị khóa/xóa khi đang dùng website B | Back-channel logout tới B trong vài giây; nếu B không nhận được, token chết sau ≤ 10 phút (TTL) và refresh bị từ chối |
| Identity down, người dùng đang ở website B | Thao tác thường chạy tiếp bằng verify offline tới khi token hết hạn; thao tác nhạy cảm bị từ chối (fail-closed) |
| Back-channel logout gửi lỗi | Retry có backoff, vào DLQ khi quá giới hạn; độ trễ vẫn bị chặn bởi TTL token |

## 6. Testing Strategy (bổ sung)

1. **Chassis conformance:** health, OpenAPI, config schema, migration, log fields, DB credential scope.
2. **Architecture fitness:** import-linter cấm import chéo; quét SQL chặn tham chiếu schema ngoài sở hữu;
   kiểm tra DB role. Test cụ thể: Analytics không có quyền trên `legacy_ingest`.
3. **Contract:** OpenAPI diff chặn breaking; characterization test cho contract `legacy`;
   test header negotiation (thiếu header = legacy, có header = envelope mới).
4. **Cutover:** diễn tập Identity single-writer trên staging, kiểm tra checksum, rollback bằng journal.
5. **Parity:** cùng fixture qua viewer cũ và analytics mới (KPI, traffic, country, group), shadow production.
6. **Chaos (docker compose):** stop analytics / reward / identity, Tool X mới, thu hồi quyền DB chéo, restart độc lập.
7. **OIDC:** Integration App mẫu chạy PKCE + `/userinfo`.
7b. **Conformance của Relying Party (Website X):** đăng ký client mới và đăng nhập thành công; khóa tài khoản thì
    bị chặn trong thời gian cam kết (back-channel ≤ vài giây, thiếu back-channel ≤ TTL); token HS256 giả mạo
    bị từ chối; token sai `aud` bị từ chối; key của app A không cấp được quyền vào app B; xoay khóa JWKS không
    làm gián đoạn; Identity down thì thao tác thường chạy, thao tác nhạy cảm bị từ chối.
8. **Dashboard conformance:** một FilterContext cho mọi widget, so sánh quốc gia chỉ ở biểu đồ riêng.

## 7. Rủi ro (cập nhật)

| # | Rủi ro | Giảm thiểu |
|---|---|---|
| R1 | Canonical model không đủ nuôi `stats_core` | Inventory ở Phase 0, parity shadow, không flip khi lệch |
| R2 | Sync server (repo khác) cần đổi credential và schema | Phối hợp với repo Tool, giữ đường ghi cũ cho đến khi xác nhận |
| R3 | Cutover Identity gây gián đoạn ngắn | Freeze vài giây, diễn tập staging, journal rollback |
| R4 | Nginx thật khác repo | Lấy cấu hình thật trước khi dựng gateway |
| R5 | Đổi HS256 → RS256 làm đứt phiên | Dual-accept ≤ 15 phút |
| R6 | Header negotiation bị bỏ quên ở client cũ | Mặc định legacy, không phá client cũ |
| R7 | Vận hành nhiều service với team nhỏ | Chassis dùng chung, template compose/pm2 |
| R8 | `stats_core` đồng bộ thủ công với repo Tool | Giữ parity test tới khi canonical store thay thế |
| R9 | Thu hồi quyền DB chéo làm lộ phụ thuộc ẩn | Chạy test này sớm ở staging từ Phase 1 |

---

## 8. Quyết định cần bạn duyệt

| # | Quyết định | Khuyến nghị |
|---|---|---|
| D1 | Legacy qua ACL trong Integration, Analytics không đọc cross-DB | **Đồng ý** (thay phương án đọc tạm của v1) |
| D2 | Change feed pull làm nền, Redis chỉ là tăng tốc | **Đồng ý** |
| D3 | Integration sở hữu Client/Credential; Identity giữ policy và cấp `client_id` | **Đồng ý** |
| D4 | Reward bỏ `accounts` ngay Phase 1 | **Đồng ý** (lệch thứ tự bạn đưa, có lý do kỹ thuật) |
| D5 | Gateway thành bước riêng 1.0, cần duyệt | **Đồng ý** |
| D6 | `git mv` thư mục sang `services/`, `apps/` đúng phase extract | **Đồng ý** |
| D7 | Permission ở Identity (`reward:access`), shim claim `discord` | **Đồng ý** |
| D8 | Contract chọn bằng header, mặc định `legacy` | **Đồng ý** |
| D9 | manager-web chuyển PKCE là tùy chọn trước Phase 6 | **Đồng ý** |
| D10 | Thu hẹp credential của sync server cần phối hợp repo `upload-facebook` | **Đã đổi theo chỉ đạo:** không cần chỉ định người. Manager chuẩn hóa hợp đồng ingest ([ingest-data-contract-v1.md](../api/ingest-data-contract-v1.md)); chủ Tool tự sửa Tool sau để chỉ đẩy đúng dữ liệu cần. Đường `documents` cũ vẫn chạy qua ACL trong lúc chuyển tiếp |
| D11 | Website mới dùng tài khoản dùng chung (SSO) hay tài khoản riêng | **SSO**, quyền theo app bằng permission `<app>:access` |
| D12 | TTL access token cho RP | **10 phút** (đánh đổi giữa độ trễ thu hồi và tải refresh) |
| D13 | Thao tác bắt buộc introspection + fail-closed | Mỗi RP tự khai báo; mặc định gồm thanh toán, đổi quyền, hành động admin, xóa dữ liệu |
| D14 | Redis dùng chung một instance với ACL và prefix riêng cho từng service, hay mỗi service một instance | **Một instance, ACL riêng** (tách instance khi cần scale); giai đoạn đầu chỉ analytics |

D1–D14 đã được bạn duyệt ngày 2026-10-05 ("giữ nguyên"), riêng D10 đổi như ghi ở trên.
Phase 0 (chỉ tài liệu và kiểm kê) và commit baseline cũng đã được duyệt.

**Quy tắc làm việc:** chỉ commit cục bộ trên nhánh `refactor/microservices`, **không push** cho tới khi hoàn thiện.
Trigger deploy tự động của hai workflow đã được tắt tạm (còn `workflow_dispatch`); bật lại ở Phase 6.
