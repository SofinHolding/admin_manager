# Integration API v1 (contract `2026-10-01`)

Bổ sung cho `docs/api/ingest-data-contract-v1.md` (hợp đồng dữ liệu) và `tasks/backlog/api-first-integration-contract-v1.md`.
Service: `integration-service` (cổng 8431, DB `integration_db`). Auth: xem `service-auth-contract-v1.md`.

## 1. Quản lý Client / Credential (mặt phẳng USER — token user RS256)

| Method & path | Quyền | Ghi chú |
|---|---|---|
| `POST /v1/integrations/clients` | user đã xác thực | body `{tool_name, client_kind: primary_tool|fallback_tool|service, platforms[]}`; owner = `sub`. `platforms`/scopes bị cắt theo policy (xem §2) |
| `GET /v1/integrations/clients` | user (của mình); admin (tất cả, lọc `owner`) | không bao giờ trả secret/hash |
| `GET /v1/integrations/clients/{client_id}` | chủ hoặc admin | kèm installations, credentials (key_id, scopes, last_used_at, status) |
| `POST /v1/integrations/clients/{client_id}/credentials` | chủ client, email đã xác thực | trả `{key_id, api_key, scopes, expires_at}` — `api_key` **chỉ một lần** |
| `POST /v1/integrations/clients/{client_id}/credentials/{key_id}/rotate` | chủ/admin | cấp key mới, key cũ còn hiệu lực `grace_seconds` (mặc định 0) |
| `DELETE /v1/integrations/clients/{client_id}/credentials/{key_id}` | chủ/admin | thu hồi tức thì → `AUTH_REVOKED`; phát `credential.revoked` |
| `PATCH /v1/integrations/clients/{client_id}` | admin | `status`, `allowed_scopes` (chỉ admin cấp `jobs:claim:system`, chỉ với `fallback_tool`) |

Alias tương thích tài liệu cũ: `POST /v1/integrations/{client_id}/credentials`.

## 2. Policy & Provisioning

- Scopes mặc định của client thường: `integration:handshake, resources:write, snapshots:write, metrics:write` (+ `jobs:claim, jobs:complete` cho `fallback_tool`).
- Client được tạo từ event `account.registered` có `provisioning` (do Identity validate theo registration key) → trạng thái
  `pending_email_verification`; sang `active` khi nhận `account.verified`; `account.status_changed→suspended` hoặc `account.deleted` → `suspended`/`deleted`
  và **mọi credential bị vô hiệu**. `client_id` do Identity cấp sẵn (`cli_...`) và Integration dùng đúng giá trị đó (idempotent theo `client_id`).
- Provision lỗi → phát `integration.client.provision_failed {client_id, account_id, error_code}`; thành công → `integration.client.provisioned`.
- Integration tự dựng `account_projection(account_id, status, email_verified)` từ feed Identity (`IDENTITY` feed qua `X-Service-Key`) — **không gọi Identity trên đường request**.

## 3. Mặt phẳng MÁY (API key `mpk_`)

Theo `ingest-data-contract-v1.md` §3 và contract api-first. Bắt buộc:

| Endpoint | Scope |
|---|---|
| `POST /v1/integrations/handshake` | `integration:handshake` |
| `PUT /v1/integrations/projects/{project_key}` | `resources:write` |
| `POST /v1/integrations/resources/batch` | `resources:write` |
| `POST /v1/integrations/snapshots` | `snapshots:write` |
| `POST /v1/integrations/snapshots/{id}/resources` · `/content` · `/events` | `snapshots:write` |
| `POST /v1/integrations/snapshots/{id}/metrics` | `metrics:write` |
| `POST /v1/integrations/snapshots/{id}/commit` | `snapshots:write` |
| `GET  /v1/integrations/snapshots/{id}` | `snapshots:write` (trạng thái, đếm, rejections) |
| `POST /v1/integrations/jobs/claim` · `/{id}/complete` · `/{id}/fail` | `jobs:claim` / `jobs:complete` (+ `jobs:claim:system` cho đa-owner) |

Hành vi bắt buộc (mỗi mục có test):

- **Ownership từ credential**: `owner_*` trong payload → `422 RECORD_VALIDATION_FAILED` (schema `additionalProperties:false`). Job do server gán owner.
- **Platform**: dữ liệu platform ngoài `allowed_platforms` của client → `403 PLATFORM_NOT_ALLOWED`.
- **Idempotency** (mọi POST/PUT ghi): `Idempotency-Key`; khoá lưu `(client_id, method, path, key) → (sha256(body), response)`.
  Cùng key + cùng body → trả **lại đúng phản hồi cũ** (không ghi lần 2); cùng key + khác body → `409 IDEMPOTENCY_CONFLICT`.
  Thiếu key ở `commit` → cho phép nhưng commit vốn idempotent theo trạng thái (commit lần 2 → `409 SNAPSHOT_ALREADY_COMMITTED` kèm id).
- **Snapshot state machine**: `building → committing → committed | failed | abandoned`. `current` theo `(owner, client, platform, project?, snapshot_type)`
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
  likes, shares, followers, interactions, videos_posted`). Thêm platform/metric = thêm dòng qua `PUT /v1/integrations/admin/platforms/{key}` / `.../metrics/{key}` (admin), không đổi code.

## 4. Jobs (Fallback Tool)

`jobs(id, owner_account_id, resource_id, kind, status pending|leased|done|failed|dead|cancelled, lease_until, attempts, claimed_by_installation, result jsonb, created_at)`.
`claim` atomic (`FOR UPDATE SKIP LOCKED`), `lease_seconds` ≤ 900, `limit` ≤ 50; lease hết hạn → tự quay về `pending` (worker bảo trì + lazy khi claim), hết lượt → `dead`;
người giữ lease cũ gặp `409 JOB_LEASE_EXPIRED`. Capability hiệu lực = giao của khai báo lúc claim và lúc handshake của chính installation. Không có
`jobs:claim:system` ⇒ chỉ claim job của **chính owner**; có ⇒ đa-owner nhưng owner do server gán. Job của owner khác hiện ra như `404 JOB_NOT_FOUND`.
`fail` với `retryable=true` còn lượt → `pending` sau backoff mũ; hết lượt (`max_attempts`) → `dead`; `retryable=false` → `failed`. `complete`/`fail` lặp lại trả `replayed=true` (idempotent).
`heartbeat` gia hạn lease. User tạo job: `POST /v1/integrations/jobs` (resource phải `execution_mode=fallback_tool`, có `dedupe_key`, hạn mức job chờ mỗi owner; body chứa `owner*` ⇒ 422).

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
