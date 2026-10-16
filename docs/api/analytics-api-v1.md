# Analytics API v1

Service: `analytics-service` (cổng 8432, DB `analytics_db`, Redis tuỳ chọn). Dữ liệu vào **chỉ** qua feed + canonical export của Integration
(`internal-feed-contract-v1.md`) và feed account của Identity. Không đọc DB Integration, không đọc payload thô của Tool.

## 1. Filter Context (duy nhất, dùng chung mọi KPI/biểu đồ)

Query params (mọi endpoint dữ liệu nhận cùng bộ này):

| Param | Ý nghĩa |
|---|---|
| `date_from`, `date_to` | `YYYY-MM-DD`, bao gồm hai đầu; mặc định 30 ngày gần nhất; khoảng đảo/dài >400 ngày → rỗng như `stats_core.days_between` |
| `owner_account_id` | chỉ `admin`/`dash_admin` được chọn người khác; người dùng thường **luôn bị ép về chính mình** (`sub`) |
| `platform` | `facebook|youtube|...` (dữ liệu, không hardcode) |
| `country` | tên quốc gia đã chuẩn hoá (alias → canonical), so khớp không phân biệt hoa/thường |
| `group_id` | nhóm kênh do viewer tạo (legacy `channel_groups`) |
| `platform_tag` | nhãn tự do của nhóm kênh (legacy). Trang Nhóm kênh **không còn cho tạo nhãn mới** (nền tảng lấy từ Platform Registry); Dashboard vẫn lọc được theo nhãn đã có, API vẫn nhận để tương thích |

Một hàm duy nhất dựng `FilterContext` từ query+principal; mọi endpoint dùng lại ⇒ KPI và chart luôn cùng bộ lọc.
Response mang `filter` (bộ lọc đã áp dụng sau khi ép quyền) và `freshness`.

## 2. Freshness

```json
"freshness": { "as_of": "2026-10-05T06:55:00Z", "committed_at": "...", "snapshot_id": "snp_..", "revision": 120, "lag_seconds": 3.2, "stale": false }
```
`as_of` = `observed_at` lớn nhất trong phạm vi lọc; `stale=true` khi `lag_seconds` > `ANALYTICS_STALE_AFTER_S` (mặc định 3600) hoặc feed nguồn không đọc được.
Dashboard luôn hiển thị được dữ liệu cũ kèm `stale`, **không** trả 500 khi nguồn chết.

## 3. Endpoint (đường dẫn mới `/v1/analytics/*`; alias legacy `/v1/viewer/*` cùng shape)

| Mới | Legacy alias | Mô tả (shape = `docs/api/legacy/admin-server.openapi.json` + `filter`,`freshness`) |
|---|---|---|
| `GET /v1/analytics/summary` | `/v1/viewer/summary` | `total_views(+_prev)`, `videos_posted(+_prev)`, `active_channels`, `managed_channels`, `days_in_range` |
| `GET /v1/analytics/traffic` | `/v1/viewer/traffic` | chuỗi theo ngày + `totals`; `metric`/`scope` như legacy |
| `GET /v1/analytics/top-channels` | `/v1/viewer/top-channels` | `{items:[{id,name,platform,country,views}]}` |
| `GET /v1/analytics/views-by-country` | `/v1/viewer/views-by-country` | **biểu đồ so sánh quốc gia riêng** |
| `GET /v1/analytics/country-tags` · `platform-tags` · `metrics` | `/v1/viewer/...` | danh mục lọc |
| `GET /v1/analytics/channel-breakdown` | `/v1/viewer/channel-breakdown` | nền tảng × quốc gia |
| `GET /v1/analytics/compare/{dimension}` | — | `dimension ∈ platform|owner|country|channel|group`; mỗi mục có `series` theo ngày + `total` |
| `GET /v1/analytics/freshness` | — | trạng thái đồng bộ (cursor, lag, dead-letter đếm) |
| `GET/POST/PATCH/DELETE /v1/analytics/groups[...]` | `/v1/viewer/groups[...]` | nhóm kênh + thành viên (`channel_ref`, `platform_tag`, `country_override`) |
| `GET/POST/PATCH/DELETE /v1/analytics/admin/country-aliases` · `country-canonicals` | `/v1/admin/country-*` | admin |
| `POST /v1/analytics/admin/cache/flush` | `/v1/admin/cache/flush` | admin |
| `POST /v1/analytics/admin/rebuild` | — | admin: đặt revision về 0 và dựng lại read model |
| `POST /v1/analytics/admin/reconcile` | — | admin: so read model với `canonical/summary`, trả chênh lệch |

Quy tắc nghiệp vụ giữ nguyên `legacy/admin-server/stats_core.py`: lượt xem FB của một ngày = tổng `views` các bài có **ngày đăng** đó (số cộng dồn hiện tại);
YouTube = `metrics.views` theo ngày; `videos_posted` (và "Số video" của biểu đồ traffic, cùng một định nghĩa) = **số bài** (`content`) theo `published_date`; ngày không có bài nào thì dùng số event `kind=post_ok` theo `occurred_date` làm dự phòng (khác hệ cũ: D5 trong `tests/contract/test_analytics_stats_parity.py`); `None≠0`; kênh quản lý/hoạt động theo `managed/active` của resource;
quốc gia: `country_override` của nhóm > quốc gia resource; "Khác" khi trống.

## 4. Cache-Aside (Redis tuỳ chọn)

Khoá `analytics:v1:{seal}:{principal_scope}:{endpoint}:{sha256(filter)}`; `seal` = revision canonical đã áp dụng + phiên bản cấu hình (alias/nhóm).
Ghi (apply revision / sửa nhóm / sửa alias) ⇒ tăng seal **sau commit PostgreSQL** ⇒ khoá cũ vô hiệu tức thì (không dựa TTL). Luôn có TTL. Redis chết ⇒ đọc thẳng
PostgreSQL, không lỗi. `scope` của người gọi nằm trong khoá để không lộ dữ liệu giữa user.

## 5. Phân quyền

Token user RS256 (offline). `viewer`: chỉ dữ liệu `owner = sub`. `admin`/`dash_admin`: mọi owner (mặc định) hoặc lọc `owner_account_id`.
Endpoint `admin/*` yêu cầu `admin|dash_admin`. Không dùng API key máy ở Analytics.

## 6. Dựng read model

Vòng lặp: đọc feed (Integration) → với `snapshot.committed` kéo `canonical/*` từ `after_revision` → upsert `read_*` → ghi `processed_events`/`feed_cursor`
cùng transaction → tăng seal. Đồng bộ định kỳ theo revision kể cả khi mất event. Feed account (Identity) nuôi `account_projection(account_id, username, role, status)`:
`account.deleted` ⇒ ẩn dữ liệu owner khỏi so sánh người; `account.status_changed→suspended` ⇒ vẫn giữ dữ liệu.
