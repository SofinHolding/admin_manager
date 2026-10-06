# 4. Dữ liệu và sự kiện

## 4.1 Đường đi của dữ liệu (Tool → Dashboard)

```text
Tool ──(API key, SDK)──► api-gateway ──► integration-service
   handshake · project · resources · snapshot(create → content/metrics/events/resources lô ≤500 → commit)
                                        │  một transaction: validate → merge(LWW, tuyệt đối) → current → outbox
                                        ▼
                         integration_db: resources, content_items, daily_metrics, activity_events, snapshots(+revision), outbox
                                        │  GET /internal/v1/canonical/{projects|resources|content|metrics|events}?after_revision=
                                        │  GET /internal/v1/canonical/summary   (head_revision + checksum + counts)
                                        │  GET /internal/v1/feed                (tín hiệu: snapshot.committed, resource.changed …)
                                        ▼
                         analytics-service (consumer, mỗi ~5 s; kéo mọi kind tới CÙNG head, áp trong MỘT transaction)
                                        ▼
                         analytics_db: read_* + sync_state(seal) ──► (Redis cache-aside) ──► /v1/analytics/* ──► manager-web
```

Event `snapshot.committed` chỉ là **tín hiệu**; dữ liệu lấy bằng canonical export theo `revision` (ADR-009). Mất event không mất dữ liệu: vòng đồng bộ luôn so `head_revision`.
Backfill = `after_revision=0`. `POST /v1/analytics/admin/rebuild` kéo hết rồi thay thế nguyên tử; `admin/reconcile` so checksum với `canonical/summary`.

## 4.2 Hợp đồng ingest (v1 `2026-10-01`) — tóm tắt, bản đầy đủ ở `docs/api/ingest-data-contract-v1.md`

| Quy tắc | Ý nghĩa vận hành |
|---|---|
| R1 upsert theo **khóa tự nhiên** | Bản ghi đã có luôn cập nhật được |
| R2 `observed_at` mới hơn thắng; bản cũ → `stale_ignored` | Gửi muộn/lệch thứ tự không làm hỏng số |
| R3 metric là **số tuyệt đối**, ghi đè, không cộng | Gửi lại không nhân đôi (E2E kiểm: 1000→1200 ⇒ tổng đổi theo) |
| R4 Tool gửi lại mọi bản ghi có metric đã đổi (cửa sổ biến động) | Trách nhiệm của Tool |
| R5 snapshot `full` + `scope`: bản ghi trong phạm vi mà vắng mặt ⇒ `removed` sau commit (giữ lịch sử, Dashboard ẩn) | E2E kiểm: bỏ kênh khỏi snapshot ⇒ biến mất |
| R6 `expected_counts` lệch ⇒ `SNAPSHOT_INCOMPLETE`, không commit | Tool lỗi giữa chừng không làm Dashboard tụt |
| R7 không bỏ sót im lặng: bản sai trả trong `rejections[]` (`code`, `index`, `external_id`); `accept_partial=false` ⇒ còn rejection thì không commit | Ví dụ thật: `METRIC_UNKNOWN` khi metric chưa có trong Metric Registry |
| R8 snapshot cũ hơn snapshot đã commit cùng phạm vi ⇒ `SNAPSHOT_SUPERSEDED` (chỉ phần suy luận removed) | |
| R9 ngày hiểu theo `report_timezone`; đổi múi giờ giữa các snapshot cùng Tool bị từ chối | |
| R10 mutation có `Idempotency-Key` (tùy chọn ở server, SDK luôn gửi) | Retry an toàn |
| `null ≠ 0` | `null` = chưa có dữ liệu; không ép thành 0 |
| Events bất biến | Bản đầu thắng |

Loại dữ liệu trong một snapshot: `resources` (page/channel/profile; `external_id`, `resource_type`, `display_name`, `country{code,name}`, `state`, `projects[]`), `content` (bài đăng: `published_date`, `metrics{views,reactions,comments,…}`),
`metrics` (số liệu theo ngày của resource: `resource_external_id`, `date`, `metrics`), `events` (`kind` như `post_ok`, `occurred_date`). Metric lạ phải được thêm vào Metric Registry (dữ liệu) hoặc đặt vào `extensions`.

Nhận dạng platform/metric/project **không có trong code**: `platform_registry`, `metric_registry` là bảng; client chỉ được ghi vào platform nằm trong `allowed_platforms` của nó.

## 4.3 Outbox và change feed (bất đồng bộ giữa service)

- Mỗi service phát event ghi `outbox` **trong cùng transaction** với thay đổi nghiệp vụ. Đọc qua `GET /internal/v1/feed?after=<seq>&limit=<1..500>&types=<csv>` với `X-Service-Key` (khóa **riêng cho từng bên đọc**).
- `seq` tăng theo **thứ tự commit**: lấy `pg_advisory_xact_lock(khóa outbox)` **trước** `nextval`, giữ tới commit. Bỏ khóa ⇒ consumer đọc theo con trỏ sẽ **mất event** (có test đo mất mát, đỏ khi gỡ khóa). Đánh đổi: các transaction ghi outbox tuần tự hóa.
- Giao hàng **at-least-once**; consumer **idempotent theo `event.id`** (`processed_events`), cursor bền (`feed_cursor`), retry/backoff có giới hạn, event "độc" vào `dead_letters`, có lệnh replay. Sự cố hạ tầng (DB/nguồn chết) **không** vào dead-letter, chỉ retry.
- Envelope: `id (evt_…), type, subject (<kind>:<id>), time (UTC), data_version (int), data{}`. Thêm trường vào `data` là tương thích; đổi/xóa trường ⇒ tăng `data_version`. Feed không chứa PII ngoài `username` (không email/hash/token).
- Redis Streams (push) **tùy chọn, chưa làm** (Phase 5). Đường bền luôn là Postgres.

### Catalog event (khớp `infra/service-registry.json`)

| Event | Phát bởi | Nhận bởi |
|---|---|---|
| `account.registered` (+`provisioning` nếu đăng ký kèm Tool) | identity | integration, analytics, reward |
| `account.verified` | identity | integration |
| `account.status_changed` | identity | integration, analytics, reward |
| `account.role_changed`, `account.profile_updated` | identity | analytics, reward |
| `account.permission_changed` | identity | reward |
| `account.deleted` | identity | integration, analytics, reward |
| `session.revoked` (`sid` vắng = mọi phiên) | identity | reward |
| `integration.client.provisioned` / `provision_failed` | integration | identity |
| `snapshot.committed`, `resource.changed` | integration | analytics |
| `credential.revoked` | integration | (chưa ai tiêu thụ) |

## 4.4 Hợp đồng đối soát Integration ↔ Analytics (đừng phá)

- `canonical/summary` trả `head_revision`, `counts` (`active` = `removed=false`) và `checksum[kind]` = sha256 các dòng đã sort (byte) `<khóa tự nhiên>|<revision>|<removed 0/1>` (+ số liệu hiển thị views/reactions/comments cho `content`, views cho `metrics`; null viết `n`).
  Khóa tự nhiên (**gồm `resource_type`**, khớp UNIQUE của Integration): `projects owner|project_key` · `resources owner|platform|resource_type|external_id` · `content owner|platform|resource_type|resource_external_id|external_id` · `metrics …|YYYY-MM-DD` · `events owner|platform|resource_type|resource_external_id|external_id`. Item canonical của content/metrics/events mang thêm `resource_type` (additive). Hai bên tính độc lập; test xuyên hai service chống lệch.
- Mỗi item canonical phải có `owner_account_id` và `platform`; kênh không có chủ đề (như YouTube) phải gửi `managed`/`active`; metric theo ngày dùng trường `date`.
- Một trang `canonical/{kind}` không được cắt ngang một revision (có thể dài hơn `limit`).
- Checksum **không phủ** tên/quốc gia của resource (giới hạn đã biết).

## 4.5 FilterContext (Analytics + Web)

Một ngữ cảnh lọc duy nhất cho **mọi** KPI và biểu đồ: `date_from, date_to` (mặc định 30 ngày; sai định dạng ⇒ 422), `owner_account_id`, `platform`, `country`, `project`, `group_id`, `platform_tag` (8 tham số chuẩn).
- Server: viewer bị ép `owner = chính mình`; admin/dash_admin xem toàn bộ. Response luôn trả lại `filter` đã áp dụng (+ `ignored_filters` khi một biểu đồ cố ý bỏ qua tham số, vd `country` ở so sánh quốc gia) và `freshness`.
- Web: `FilterContext.tsx` là nguồn sự thật duy nhất, đồng bộ hai chiều với URL; đổi bộ lọc ⇒ **tất cả** biểu đồ đổi cùng nhau; so sánh quốc gia nằm ở biểu đồ riêng.

## 4.6 Cache (Redis, Cache-Aside)

- Đọc: lấy `seal` từ PostgreSQL → `GET` cache → HIT trả về / MISS → tính từ PG → `SET` **luôn có TTL** (mặc định 300 s). Ghi (apply revision, sửa nhóm/alias, flush): thay đổi + tăng `seal` **cùng transaction PG**; khóa cũ tự mồ côi.
- Khóa: `analytics:v1:{seal}:{own:<owner>|all}:{endpoint}:{sha256(filter)}`. Scope người gọi nằm trong khóa (không nằm trong hash) để không thể lộ dữ liệu giữa user.
- Redis chết ⇒ rơi về PostgreSQL, `X-Cache: OFF`, vẫn đúng. `/health/ready` **không** phụ thuộc Redis. Redis ACL: user `default` tắt, `analytics_cache` chỉ prefix `analytics:*`, `-@dangerous` (D14: một Redis, ACL theo service).
- Sơ đồ: `docs/architecture/cache-aside-read-sequence.html`, `cache-aside-write-sequence.html`.

## 4.7 Cô lập dữ liệu

| Tài nguyên | Cách cô lập |
|---|---|
| Postgres | 1 cluster DEV, 4 database + 4 user không superuser; `REVOKE ALL … FROM PUBLIC`, `GRANT CONNECT` chỉ cho chủ. Test 4×4 (arch) + 12 cặp (e2e) chứng minh kết nối chéo bị `InsufficientPrivilege` |
| Redis | ACL theo prefix |
| Hạ tầng DEV | Compose project `manager-platform-dev`, container `mp-dev-postgres` (`127.0.0.1:55440`), `mp-dev-redis` (`127.0.0.1:56390`), network `mp_dev_net`. Test guard chặn tên chung chung (đã từng ghi đè container của project khác) |
