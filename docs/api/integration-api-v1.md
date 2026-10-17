# Integration API v1 (contract `2026-10-01`)

Bổ sung cho `docs/api/ingest-data-contract-v1.md` (hợp đồng dữ liệu) và `tasks/backlog/api-first-integration-contract-v1.md`.
**Tool đã liên kết cần sửa gì (mã nền tảng, bỏ `PUT /projects`, định dạng job/snapshot của collector):** xem `docs/api/integration-tool-changes-2026-10-16.md`.
Service: `integration-service` (cổng 8431, DB `integration_db`). Auth: xem `service-auth-contract-v1.md`.

## 1. Quản lý Client / Credential (mặt phẳng USER — token user RS256)

Hai loại client (`ownership`), mỗi loại đúng MỘT đường tạo:

- **personal** (`client_kind=primary_tool`): Tool cá nhân của một tài khoản. CHỈ sinh ra khi tài khoản được cấp
  `capability:tool_integration` (event `account.permission_changed` từ Identity — gói quyền mã mời `viewer_tool` hoặc admin cấp
  trong Phân quyền). Mỗi tài khoản tối đa 1 client personal đang `pending_email_verification|active` (unique index).
- **shared** (`client_kind=fallback_tool|service`): công cụ dùng chung, CHỈ role `admin` tạo qua wizard bắt buộc
  `draft → configured → active`; owner = admin chịu trách nhiệm.

| Method & path | Quyền | Ghi chú |
|---|---|---|
| `POST /v1/integrations/clients` | `admin` | Bước 1 wizard: `{tool_name, client_kind: fallback_tool|service, purpose, platforms?}` → client shared `draft`. `primary_tool` → 422 |
| `PUT /v1/integrations/clients/{client_id}/config` | `admin` | Bước 2, chỉ khi `draft`: `{allowed_platforms, allowed_scopes}` → `configured_at` |
| `POST /v1/integrations/clients/{client_id}/activate` | `admin` | Bước 3: `{confirm_tool_name}` phải khớp `tool_name`; cần bước 2 → `active` |
| `POST /v1/integrations/clients/{client_id}/transfer` | `admin` | chỉ personal: `{new_owner_account_id}`; thu hồi mọi khoá, đổi chủ, cấp khoá chờ chủ mới nhận. Dữ liệu đã ingest GIỮ owner cũ |
| `GET /v1/integrations/clients` | user (của mình); admin/dash_admin (tất cả, lọc `owner`) | mỗi item kèm `ownership`, `purpose`, `credentials` (tóm tắt: `key_id, status, reveal_state, bound_device_id, activated_at, last_used_at, expires_at`) |
| `GET /v1/integrations/clients/{client_id}` | chủ hoặc admin | kèm installations, credentials |
| `POST /v1/integrations/clients/{client_id}/credentials` | chủ / `admin` | client `draft` → `409 CLIENT_NOT_READY`. Chủ nhận `api_key` một lần. Admin cấp trên client personal → `api_key=null`, `reveal_state=pending` |
| `POST /v1/integrations/clients/{client_id}/credentials/{key_id}/rotate` | chủ / `admin` | cùng quy tắc plaintext như trên; key cũ còn hiệu lực `grace_seconds` (mặc định 0). Khoá mới chưa bind thiết bị |
| `DELETE /v1/integrations/clients/{client_id}/credentials/{key_id}` | chủ / `admin` | thu hồi tức thì → `AUTH_REVOKED`; phát `credential.revoked` |
| `PATCH /v1/integrations/clients/{client_id}` | `admin` | `status`, `allowed_scopes`, `allowed_platforms`, `tool_name`; `draft → active` qua PATCH → `409 CLIENT_NOT_READY` |
| `POST /v1/integrations/me/credential/reveal` | chủ client personal | nhận plaintext khoá `reveal_state=pending` MỘT lần (xoá bản mã hoá). Hết hạn → `410 REVEAL_EXPIRED`; không có → `404 REVEAL_NOT_AVAILABLE`; email chưa xác thực → `403 EMAIL_NOT_VERIFIED` |
| `GET /v1/integrations/me/connection-guide` | chủ client personal | base URL công khai (`INTEGRATION_PUBLIC_BASE_URL`), header bắt buộc, khoá (không có secret), thiết bị đã kích hoạt, endpoint máy mà scope khoá cho phép (kèm request schema đã resolve, ví dụ, mã lỗi), mã lỗi + HTTP status, giới hạn, 5 snapshot gần nhất + rejections |
| `GET /v1/integrations/me/openapi.json` | chủ client personal | OpenAPI rút gọn đúng các endpoint trong connection-guide |

Alias tương thích tài liệu cũ: `POST /v1/integrations/{client_id}/credentials`.

## 2. Policy & Provisioning

- Scopes mặc định: `integration:handshake, resources:write, snapshots:write, metrics:write` (+ `jobs:claim, jobs:complete` cho `fallback_tool`;
  `jobs:claim:system` chỉ admin cấp cho `fallback_tool`).
- `account.permission_changed(capability:tool_integration, granted=true)` → tạo client personal (`tool_name="Tool cá nhân"`, mọi platform đang bật)
  hoặc kích hoạt lại client đã bị tạm dừng do thu hồi capability; nếu chưa có khoá active → tạo 1 khoá `reveal_state=pending`
  (secret mã hoá Fernet bằng `INTEGRATION_REVEAL_KEY`, hết hạn sau `CREDENTIAL_REVEAL_TTL_DAYS`, mặc định 7). Idempotent. Phát `integration.client.provisioned`.
- `granted=false` → client personal `suspended` (`suspended_by=capability`) và **mọi khoá bị thu hồi**.
- Client personal ở `pending_email_verification` cho tới `account.verified`; `account.status_changed→suspended` / `account.deleted` → `suspended`/`deleted` + thu hồi khoá.
- Integration tự dựng `account_projection(account_id, status, email_verified)` từ feed Identity (`X-Service-Key`) — **không gọi Identity trên đường request**.
- Plaintext khoá chỉ đến tay CHỦ client personal (trả trực tiếp hoặc qua reveal) hoặc admin với client shared. Admin không bao giờ thấy secret khoá cá nhân.

## 3. Mặt phẳng MÁY (API key `mpk_`)

Theo `ingest-data-contract-v1.md` §3 và contract api-first. Bắt buộc:

| Endpoint | Scope |
|---|---|
| `POST /v1/integrations/handshake` | `integration:handshake` |
| `POST /v1/integrations/resources/batch` | `resources:write` |
| `POST /v1/integrations/snapshots` | `snapshots:write` |
| `POST /v1/integrations/snapshots/{id}/resources` · `/content` · `/events` | `snapshots:write` |
| `POST /v1/integrations/snapshots/{id}/metrics` | `metrics:write` |
| `POST /v1/integrations/snapshots/{id}/commit` | `snapshots:write` |
| `GET  /v1/integrations/snapshots/{id}` | `snapshots:write` (trạng thái, đếm, rejections) |
| `POST /v1/integrations/jobs/claim` · `/{id}/complete` · `/{id}/fail` | `jobs:claim` / `jobs:complete` (+ `jobs:claim:system` cho đa-owner). `claim` nhận thêm `resource_ids` (tuỳ chọn): chỉ nhận job của các kênh đó |
| `POST /v1/integrations/jobs/enqueue` | `jobs:claim:system` (chỉ `client_kind=fallback_tool`): xin tạo job `collect` cho các kênh ĐÃ KÉO (≤200/lần); kênh đã có job pending/leased → `already_active`; kênh không hợp lệ → `skipped[].reason` (`NOT_FOUND`/`NOT_ELIGIBLE`/`PLATFORM_NOT_ALLOWED`/`LIMIT_EXCEEDED`). Xem `docs/architecture/shared-collector-schedule-v1.md` |
| `POST /v1/integrations/workers/register` · `/{worker_id}/heartbeat` | `jobs:claim` (chỉ `client_kind=fallback_tool`) — PHASE 4, chỉ in-memory (xem §4) |

Hành vi bắt buộc (mỗi mục có test):

- **Device binding (1 khoá : 1 thiết bị)**: `handshake` đầu tiên bind khoá vào `device_id` đó (`bound_device_id`, `bound_installation_id`, `activated_at`).
  Handshake từ thiết bị khác → `409 CREDENTIAL_DEVICE_BOUND` (không tạo installation). Mọi endpoint máy khác `/handshake`: khoá chưa bind →
  `403 CREDENTIAL_NOT_ACTIVATED`; header `X-Installation-Id` vắng/khác installation đã bind → `403 INSTALLATION_MISMATCH`; `jobs/claim.installation_id`
  phải trùng installation đã bind. Đổi máy = xoay khoá (khoá mới chưa bind). Nhiều worker = nhiều khoá.
- **Client dùng chung không ghi dữ liệu đứng tên mình**: client `ownership=shared` gọi `POST /resources/batch`, tạo/đẩy/commit
  snapshot mà thiếu `X-Delegated-Job` hợp lệ → `403 SHARED_CLIENT_REQUIRES_DELEGATION`. Đọc (`GET /resources`, `/resource-owners`) và jobs không đổi.
- **Ownership từ credential**: `owner_*` trong payload → `422 RECORD_VALIDATION_FAILED` (schema `additionalProperties:false`). Job do server gán owner.
- **Platform + mã nền tảng**: mỗi nền tảng (tên hiển thị) có MỘT mã chính (`key`) và nhiều mã phụ (`aliases`); mọi mã là DUY NHẤT toàn hệ thống (không trùng chính/phụ, kiểm trong transaction có khoá).
  Mọi snapshot/resource/handshake/claim BẮT BUỘC kèm `platform` = MỘT mã bất kỳ của nền tảng (chính hoặc phụ); server chuẩn hoá về mã chính TRƯỚC khi kiểm quyền và lưu, nên số liệu luôn được lưu/hiển thị
  theo đúng nền tảng (`snapshot.platform` trong phản hồi là mã chính). Mã lạ, mã phụ đã gỡ, nền tảng bị tắt, hoặc nền tảng ngoài `allowed_platforms` của client → `403 PLATFORM_NOT_ALLOWED` (không lưu gì).
  Tool hỏi danh sách mã bằng `GET /v1/integrations/platforms` (scope `integration:handshake`; = `allowed_platforms` ∩ registry đang bật; trả `[{key, display_name, enabled, aliases, codes}]`, `codes = [key, ...aliases]`).
  Tool **cá nhân** tự được thêm mọi platform Admin/dash_admin tạo (bật); Tool **dùng chung** chỉ nhận platform admin chọn khi cấu hình client (cũng dùng được mọi mã của nền tảng đó).
- **Tool có dữ liệu của NHIỀU nền tảng**: mỗi snapshot chỉ chứa dữ liệu của MỘT nền tảng (trường `platform` của snapshot áp cho mọi bản ghi trong nó — `content/metrics/events` tham chiếu kênh theo `(owner, platform, resource_external_id)` của chính nền tảng đó,
  nên hai nền tảng trùng `external_id` kênh không lẫn nhau). Tool gửi MỘT snapshot cho mỗi nền tảng, mỗi snapshot kèm mã nền tảng của dữ liệu đó (SDK: `IntegrationClient.push_by_platform`). Mã sai/thiếu → không tạo snapshot, không lưu gì
  (`resources/batch` trộn mã đúng và sai bị từ chối CẢ LÔ). `GET /v1/integrations/me/connection-guide` liệt kê `platforms[].codes` của client, gắn danh sách mã vào `enum` của mọi trường `platform`/`platforms` trong `request_schema`, và nêu `platform_rules`.
- **Idempotency** (mọi POST/PUT ghi): `Idempotency-Key`; khoá lưu `(client_id, method, path, key) → (sha256(body), response)`.
  Cùng key + cùng body → trả **lại đúng phản hồi cũ** (không ghi lần 2); cùng key + khác body → `409 IDEMPOTENCY_CONFLICT`.
  Thiếu key ở `commit` → cho phép nhưng commit vốn idempotent theo trạng thái (commit lần 2 → `409 SNAPSHOT_ALREADY_COMMITTED` kèm id).
- **Snapshot state machine**: `building → committing → committed | failed | abandoned`. `current` theo `(owner, client, platform, snapshot_type)`
  chỉ đổi khi commit thành công. Snapshot `building` quá `SNAPSHOT_TTL` (cấu hình, mặc định 6h) → worker đánh `abandoned`.
  Tool chết giữa chừng ⇒ canonical không đổi.
- **Commit atomic**: một transaction: kiểm `expected_counts` (lệch → `422 SNAPSHOT_INCOMPLETE`, không đổi gì), kiểm `rejections` (còn → `SNAPSHOT_INCOMPLETE`
  trừ `accept_partial=true`), merge staging→canonical (LWW theo `observed_at`; bản cũ hơn → `stale_ignored`), suy luận `removed` cho snapshot `full` theo `scope`,
  gán `revision`, ghi outbox `snapshot.committed`. `SNAPSHOT_SUPERSEDED`: commit `full` có `observed_at` cũ hơn snapshot đã commit cùng phạm vi chỉ bỏ phần suy luận removed.
- **null ≠ 0**: lưu và xuất đúng; không ép.
- **Giới hạn**: ≤500 bản ghi/batch (`BATCH_TOO_LARGE`), body ≤ 2 MiB (`413`), metric key theo `Metric Registry`; khoá lạ → `extensions`.
- **Rate limit** theo `client_id`: mặc định 600 request/phút (cấu hình); vượt → `429 RATE_LIMITED` + `Retry-After`.
- **Audit** `audit_log(account_id, client_id, installation_id, key_id, action, snapshot_id?, request_id, ip, result, at)`; admin xem `GET /v1/integrations/audit`.
- **Registry**: `platform_registry` và `metric_registry` là DỮ LIỆU (seed trong migration: platforms `facebook, youtube, tiktok, instagram`; metrics `views, reactions, comments,
  likes, shares, followers, interactions, videos_posted`). Thêm platform = `POST /v1/integrations/admin/platforms` (role `admin` **hoặc `dash_admin`**; body `{key, display_name, aliases?}`; mã đã tồn tại → `409 PLATFORM_CODE_EXISTS`);
  sửa tên/bật-tắt = `PUT .../platforms/{key}` (không xoá, chỉ tắt); mã phụ = `POST .../platforms/{key}/aliases` · `DELETE .../platforms/{key}/aliases/{alias}`; thêm metric = `PUT .../metrics/{key}` (chỉ `admin`). Không đổi code. Platform Registry là NGUỒN SỰ THẬT về "nền tảng nào tồn tại":
  nhập tay trên web (`/v1/resources/platforms`, kèm `display_name`) và `legacy_ingest.platform_policies` (hành vi `web_entry_enabled`/`fallback_enabled`, chỉ chỉnh được platform đã
  có trong registry; platform mới mặc định `web_entry=true, fallback=true`: kênh nhập tay được giao cho Tool dùng chung kéo số liệu, collector chỉ nhận platform nó có adapter nên job platform chưa hỗ trợ nằm chờ) đều lấy theo registry.
- **Delegated ingestion** (PHASE 4, Shared Collector — `docs/architecture/shared-collector-design-v1.md`): header tuỳ chọn `X-Delegated-Job: <job_id>`
  trên MỌI endpoint snapshot (`POST /snapshots`, batch, `commit`, `GET /snapshots/{id}`). Chỉ `client_kind=fallback_tool`; job phải đang `leased` bởi
  ĐÚNG client đó và có `resource_owner_id` (gán lúc tạo job `kind=collect`, xem §4) → owner dữ liệu = `job.resource_owner_id` thay vì owner của credential.
  Vắng header: hành vi y hệt trước PHASE 4. Job không thoả điều kiện → `404 JOB_NOT_FOUND` / `409 JOB_INVALID_STATE`; sai `client_kind` → `403 SCOPE_DENIED`.
  **Ánh xạ kênh/nền tảng (bắt buộc khi có header):** `platform` của snapshot/resource PHẢI bằng `job.platform` (`403 PLATFORM_NOT_ALLOWED`); chỉ `snapshot_type=delta` (`full` kèm scope sẽ đánh
  dấu removed các kênh Tool không thấy → `422`); không tạo/sửa resource qua snapshot (`403 SCOPE_DENIED`); mỗi bản ghi `content/metrics/events` PHẢI có `resource_external_id` = `external_id` của
  resource trong job (khác → `rejections[].code=RESOURCE_UNKNOWN`, không lưu). Nhờ đó số liệu luôn rơi đúng kênh + đúng nền tảng user đã nhập, dù Tool tự dò ra id khác (vd `UC…` của YouTube).
- **File kết nối cho key dùng chung**: khi Admin cấp credential cho client dùng chung, giao diện cho tải `connection-<client_id>.json`
  `{format:"manager-connection/1", gateway, tool_name, client_id, key_id, api_key, allowed_platforms, contract_version, issued_at}` (chứa secret, tạo ở trình duyệt, chỉ có lúc cấp/xoay). Collector chạy
  `python collect.py --connection-file connection-<client_id>.json` (Electron: nút "Nạp file kết nối…"). Key cần scope `jobs:claim:system` để nhận job của mọi user.

## 4. Jobs (Fallback Tool)

`jobs(id, owner_account_id, resource_id, kind, status pending|leased|done|failed|dead|cancelled, lease_until, attempts, claimed_by_installation, result jsonb, created_at)`.
`claim` atomic (`FOR UPDATE SKIP LOCKED`), `lease_seconds` ≤ 900, `limit` ≤ 50; lease hết hạn → tự quay về `pending` (worker bảo trì + lazy khi claim), hết lượt → `dead`;
người giữ lease cũ gặp `409 JOB_LEASE_EXPIRED`. Capability hiệu lực = giao của khai báo lúc claim và lúc handshake của chính installation. Không có
`jobs:claim:system` ⇒ chỉ claim job của **chính owner**; có ⇒ đa-owner nhưng owner do server gán. Job của owner khác hiện ra như `404 JOB_NOT_FOUND`.
`fail` với `retryable=true` còn lượt → `pending` sau backoff mũ; hết lượt (`max_attempts`) → `dead`; `retryable=false` → `failed`. `complete`/`fail` lặp lại trả `replayed=true` (idempotent).
`heartbeat` gia hạn lease. User tạo job: `POST /v1/integrations/jobs` (resource phải `execution_mode=fallback_tool`, có `dedupe_key`, hạn mức job chờ mỗi owner; body chứa `owner*` ⇒ 422).
Cột `jobs.resource_owner_id` (PHASE 4, migration `0006`): owner THỰC của dữ liệu khi collector push hộ qua `X-Delegated-Job` (xem §3). `NULL` với mọi
job tạo trước PHASE 4 và mọi `kind` khác `collect` — không hỗ trợ delegated ingestion, hành vi không đổi. Job `kind=collect` tự tạo khi user có
`capability:manual_channel_mgmt` tự khai báo kênh (`POST /v1/resources`, xem §5) luôn có `resource_owner_id = owner_account_id`.
`POST /v1/integrations/workers/register` · `/{worker_id}/heartbeat` (PHASE 4): chỉ xác thực credential (`client_kind=fallback_tool`, scope `jobs:claim`)
rồi trả `{registration_id, heartbeat_interval_s}` / `{ok, next_heartbeat_in_s}`. CHỈ IN-MEMORY — chưa có bảng lưu trữ, chưa theo dõi miss heartbeat
(việc tương lai); Shared Collector hiện dựa vào lease TTL của job để tự phục hồi khi chết giữa chừng.

## 5. Tương thích legacy (Phase 2b)

`/v1/resources/*`, `/v1/ops/*`, `/v1/viewer/links*` (26 operation) giữ **nguyên đường dẫn, query, body, status và lỗi `{detail}`** như `admin-server`
(`docs/api/legacy/admin-server.openapi.json`), phục vụ `admin-web` và `local-ops-hub` hiện tại. Đây là lớp chống ăn mòn (`legacy_acl`): mọi ghi
đi qua cùng `ingest.merge` vào kho canonical (một đường ghi, cùng `revision`, cùng event `resource.changed`), nên Analytics thấy dữ liệu nhập tay y như dữ liệu Tool đẩy lên.
Schema `legacy_ingest` nằm TRONG `integration_db` và dùng chính user `integration_svc` (KHÔNG có DB role `legacy_ingest_writer` ở giai đoạn này).
Xác thực bằng JWT user RS256; body có `owner*` ⇒ 422 (legacy lặng lẽ bỏ qua — khác biệt có chủ đích). Legacy chỉ thấy/sửa resource có `origin <> 'tool'`.
Chưa làm (cần hạ tầng/sync server, chờ duyệt): canonicalizer `documents → snapshot` (ingest-data-contract §7).

## 6. Endpoint nội bộ (service key, không qua gateway)

`GET /internal/v1/feed` · `GET /internal/v1/canonical/{kind}` · `GET /internal/v1/canonical/summary` — xem `internal-feed-contract-v1.md`.

## 7. OpenAPI

Mọi endpoint công khai có request/response schema, `error.code`, scope, ví dụ; `/openapi.json` đúng thực tế (có test contract so sánh với bản đóng băng
`docs/api/openapi/integration-service.v1.json`). Thay đổi phá vỡ = contract version mới.

## 8. Thay đổi 2026-10-14 (trước phát hành)

integration-service chưa chạy production (Phase 6 chưa duyệt) nên các thay đổi sau được áp TRỰC TIẾP vào contract `2026-10-01` thay vì mở
version mới; bản OpenAPI đóng băng đã sinh lại. Tool/SDK đang dùng bản cũ PHẢI cập nhật:

| Thay đổi | Ảnh hưởng tới Tool / UI |
|---|---|
| Device binding: 1 khoá chỉ kích hoạt cho 1 thiết bị ở handshake đầu tiên; `X-Installation-Id` bắt buộc ngoài `/handshake` | Gọi `/handshake` trước mọi việc, gửi `X-Installation-Id` mọi request sau (`manager_sdk.IntegrationClient` tự làm). Đổi máy/nhiều worker = xoay/cấp thêm khoá |
| Client personal chỉ tự sinh từ `capability:tool_integration`; user không còn `POST /clients` | Bỏ luồng tự tạo client; người dùng nhận khoá qua `POST /me/credential/reveal` hoặc tự cấp khi không còn khoá active |
| `POST /clients` thành bước 1 wizard client dùng chung (admin), thêm `PUT /clients/{id}/config`, `POST /clients/{id}/activate`; trạng thái `draft` | Không cấp được khoá cho client chưa kích hoạt (`409 CLIENT_NOT_READY`) |
| Admin cấp/xoay khoá trên client personal không trả `api_key` (`reveal_state=pending`) | Admin không thấy secret khoá cá nhân |
| `POST /clients/{id}/transfer` | Chuyển chủ client personal; dữ liệu đã ingest giữ owner cũ |
| Client dùng chung ghi dữ liệu phải kèm `X-Delegated-Job` | `403 SHARED_CLIENT_REQUIRES_DELEGATION` |
| `GET /me/connection-guide`, `GET /me/openapi.json` | Tài liệu tích hợp tự mô tả cho chủ Tool cá nhân |
| Bỏ provisioning lúc đăng ký (`account.registered.provisioning`) và event `integration.client.provision_failed` | Identity không còn nhận `integration` khi đăng ký |
| Mã lỗi mới | `CREDENTIAL_DEVICE_BOUND`, `CREDENTIAL_NOT_ACTIVATED`, `INSTALLATION_MISMATCH`, `SHARED_CLIENT_REQUIRES_DELEGATION`, `CLIENT_NOT_READY`, `REVEAL_NOT_AVAILABLE`, `REVEAL_EXPIRED`, `PERSONAL_CLIENT_EXISTS` |

Giới hạn đã biết: `installation_id` không phải bí mật — chép CẢ khoá lẫn `installation_id` sang máy khác thì vẫn gọi được; binding chặn kích
hoạt lần hai và dùng nhầm, không thay cho việc giữ bí mật khoá.
