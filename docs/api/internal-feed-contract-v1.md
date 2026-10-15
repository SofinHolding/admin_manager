# Internal Feed & Canonical Export Contract v1

Bất đồng bộ giữa service = **transactional outbox + change feed kéo (pull)**. Redis/broker (nếu có) chỉ là tăng tốc; đường bền là Postgres.

## 1. Feed

`GET /internal/v1/feed?after=<seq>&limit=<1..500>&types=<csv>` — Header `X-Service-Key`.

```json
{ "events": [ { "seq": 41, "id": "evt_9c1f...", "type": "account.status_changed", "subject": "acc:3f..",
                "time": "2026-10-05T10:00:00Z", "data_version": 1, "data": { } } ],
  "next_cursor": 41, "has_more": false, "head": 41 }
```

- `seq` tăng nghiêm ngặt **theo thứ tự commit**. Cách đảm bảo: ghi outbox trong cùng transaction với thay đổi nghiệp vụ, và lấy
  `pg_advisory_xact_lock(<khoá outbox của service>)` **trước** `nextval`, giữ tới commit — nên transaction sau không thể có `seq` nhỏ hơn
  mà commit muộn hơn (tránh mất sự kiện khi consumer đọc theo con trỏ). Đánh đổi: các transaction ghi outbox được tuần tự hoá; chấp nhận
  ở quy mô này.
- Giao hàng **at-least-once**. Consumer idempotent theo `id`. Con trỏ lưu bền ở consumer (`feed_cursor(source, seq)`).
- `after` mặc định 0 (replay/backfill từ đầu). Retention outbox: không xoá trong v1.
- Phản hồi không chứa PII ngoài `username`; không có email, hash, token.

## 2. Envelope sự kiện (mọi service)

`id` (`evt_` + uuid4 hex), `type`, `subject` (`<kind>:<id>`), `time` (UTC RFC3339), `data_version` (int, bắt đầu 1), `data` (object).
Thêm trường vào `data` là tương thích; đổi/xoá trường = tăng `data_version`.

## 3. Sự kiện của Identity (`/internal/v1/feed` của identity-service)

| type | data |
|---|---|
| `account.registered` | `account_id, username, role, status` |
| `account.verified` | `account_id` |
| `account.status_changed` | `account_id, status, previous` |
| `account.role_changed` | `account_id, role, previous` |
| `account.profile_updated` | `account_id, username` |
| `account.permission_changed` | `account_id, permission, granted(bool)` |
| `account.deleted` | `account_id` |
| `session.revoked` | `account_id, sid?` (`sid` vắng = mọi phiên) |

## 4. Sự kiện của Integration

| type | data |
|---|---|
| `snapshot.committed` | `snapshot_id, owner_account_id, client_id, platform, snapshot_type, observed_at, committed_at, report_timezone, counts{resources,content,metrics,events}, removed{...}, revision_from, revision_to` |
| `resource.changed` | `resource_id, owner_account_id, platform, external_id, state, revision` (đổi ngoài snapshot, vd admin bật/tắt) |
| `integration.client.provisioned` | `client_id, account_id, status` (Tool cá nhân được tạo/kích hoạt lại khi tài khoản được cấp `capability:tool_integration`) |
| `credential.revoked` | `client_id, key_id` |

## 5. Canonical Export (Integration → Analytics)

Analytics **không đọc DB Integration và không đọc payload thô**. Nó kéo trạng thái canonical đã commit:

`GET /internal/v1/canonical/{kind}?after_revision=<r>&limit=<1..1000>` — `kind ∈ projects | resources | content | metrics | events`.

```json
{ "items": [ { "revision": 120, "removed": false, ... trường canonical của kind ... } ],
  "next_revision": 120, "has_more": false, "head": 120 }
```

- Mỗi bản ghi canonical có `revision` (bigint toàn cục, gán **lúc commit snapshot**, cùng transaction). Bản ghi bị loại (vắng mặt trong snapshot
  full / đánh dấu xoá) có `removed: true` (lịch sử giữ lại).
- Chỉ trả dữ liệu của snapshot **đã commit**. Snapshot building/failed/abandoned không bao giờ xuất hiện.
- `GET /internal/v1/canonical/summary` → `{head_revision, counts{kind:{total,active}}, checksum{kind: "<sha256 hex>"}, checksum_algorithm}` để Analytics đối soát.
  `checksum[kind]` = sha256 (UTF-8) của các dòng sort theo byte nối bằng LF; mỗi dòng `<khoá tự nhiên>|<revision>|<0/1 removed>[|<số liệu hiển thị>]` với khoá tự nhiên KHÔNG dùng id nội bộ:
  `projects owner|project_key` · `resources owner|platform|resource_type|external_id` · `content owner|platform|resource_type|resource_external_id|external_id` + `|views,reactions,comments` · `metrics owner|platform|resource_type|resource_external_id|YYYY-MM-DD` + `|views` · `events owner|platform|resource_type|resource_external_id|external_id` (null viết `n`).
- Trường canonical theo `docs/api/ingest-data-contract-v1.md` mục 4, thêm: `owner_account_id`, `client_id`, `platform`, `project_keys` (resources),
  `resource_id`/`resource_external_id`/`resource_type` (content, metrics, events — `resource_type` của resource cha, additive), `observed_at`, `revision`, `removed`.

Analytics: đọc feed để **biết có gì mới** (event `snapshot.committed`), rồi kéo `canonical/*` từ `after_revision` đã lưu. Mất event cũng không
sao vì cuối cùng vẫn đồng bộ theo revision (backfill = đặt revision về 0).

## 6. Hành vi consumer bắt buộc

- Idempotent: bảng `processed_events(id pk)`; xử lý lại event đã thấy là no-op.
- Retry có backoff mũ + jitter, giới hạn số lần; quá giới hạn → ghi `dead_letters(id, type, error, attempts, payload)` và **tiếp tục** event sau
  (không chặn hàng đợi). Có lệnh replay dead-letter.
- Nguồn feed chết: consumer giữ nguyên con trỏ, tự thử lại, không crash, không làm service không-liên-quan lỗi.
- Hoạt động phải đúng khi Redis không có.
