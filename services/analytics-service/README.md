# analytics-service

Toàn bộ thống kê và Dashboard: KPI, traffic, nền tảng, cá nhân, quốc gia, kênh, nhóm, freshness.

| | |
|---|---|
| Cổng | 8432 |
| Database (riêng) | `analytics_db` |
| Package | `analytics_service` |
| Trạng thái | **Phase 3 backend: đã hiện thực** (mọi module `active`) |
| Đặc tả | `docs/api/analytics-api-v1.md`, `docs/api/internal-feed-contract-v1.md`, `docs/api/openapi/analytics-service.v1.json` |

## Module

| Module | Trách nhiệm |
|---|---|
| `filters` | `FilterContext` duy nhất + `FilterQuery` (khai báo query-param một lần cho mọi endpoint dữ liệu). Người thường bị ÉP về `sub` ở đây |
| `feed_consumer` | kéo feed Integration/Identity, kéo `canonical/*` theo revision, áp nguyên tử, idempotent, retry/dead-letter, rebuild, reconcile |
| `readmodels` | `read_*` tables, dựng dataset + chọn tập kênh (`dataset.py`), tổng hợp theo ngày (`aggregate.py`), đường chạy chung `query.py` |
| `kpi` | `summary` (tổng lượt xem + kỳ trước, video đăng, kênh quản lý/hoạt động) |
| `traffic` | `traffic` (views/interactions/comments/videos theo ngày), `top-channels`, `metrics` |
| `breakdown` | `channel-breakdown` và `compare/{dimension}` |
| `country_comparison` | `views-by-country` (biểu đồ so sánh quốc gia RIÊNG), `country-tags` |
| `groups` | nhóm kênh + thành viên + `platform-tags` |
| `country_config` | alias / tên chuẩn quốc gia (admin) |
| `freshness` | khối `freshness` + `GET /v1/analytics/freshness` |
| `cache` | Cache-Aside Redis (tuỳ chọn) |

## Dữ liệu và sự kiện

- Bảng sở hữu (registry): `read_projects`, `read_resources`, `read_content`, `read_daily_metrics`, `read_events`, `agg_daily` (chưa dùng), `channel_groups`, `channel_group_members`, `country_aliases`, `country_canonicals`, `feed_cursor`, `processed_events`, `dead_letters`, `sync_state`, `account_projection`
- Migration: `migrations/0001_read_models.sql` (chạy bằng job riêng `python -m analytics_service.migrate`, không chạy lúc khởi động)
- Nhận event: `snapshot.committed`, `resource.changed` (Integration) và `account.registered|status_changed|role_changed|profile_updated|deleted` (Identity). Không phát event.

## Endpoint

Mọi endpoint dữ liệu nhận CÙNG bộ query (`date_from`, `date_to`, `owner_account_id`, `platform`, `country`, `project`, `group_id`, `platform_tag`)
và trả `filter` (đã áp dụng sau khi ép quyền) + `freshness`. Thành công: cùng shape ở đường dẫn mới và alias legacy.
Lỗi: envelope `{ok:false,error,meta}` ở `/v1/analytics/*`; `{"detail": ...}` ở `/v1/viewer/*`, `/v1/admin/*`.

| Mới | Alias legacy |
|---|---|
| `GET /v1/analytics/{summary,traffic,top-channels,views-by-country,country-tags,platform-tags,metrics,channel-breakdown}` | `/v1/viewer/<cùng tên>` |
| `GET /v1/analytics/compare/{platform\|owner\|country\|channel\|group\|project}` | — |
| `GET /v1/analytics/freshness` | — |
| `GET/POST/PATCH/DELETE /v1/analytics/groups[...]` | `/v1/viewer/groups[...]` |
| `GET/POST/PATCH/DELETE /v1/analytics/admin/country-aliases`, `country-canonicals` | `/v1/admin/country-*` |
| `POST /v1/analytics/admin/cache/flush` | `/v1/admin/cache/flush` |
| `POST /v1/analytics/admin/{rebuild,reconcile,sync}`, `GET /v1/analytics/admin/dead-letters`, `POST .../dead-letters/replay` | — |

Header `X-Cache: HIT|MISS|OFF` cho biết kết quả có từ cache hay không (OFF = không cấu hình Redis).

## Quyết định thiết kế

- **Seal trong PostgreSQL.** `sync_state('seal')` tăng TRONG CÙNG transaction với mọi thay đổi số liệu (áp revision, sửa nhóm, sửa alias/tên chuẩn,
  flush). Mỗi request đọc seal từ PostgreSQL TRƯỚC khi tra cache nên khoá cũ vô hiệu tức thì, kể cả khi Redis mất hay được khởi động lại
  (seal không sống trong Redis). Khoá: `analytics:v1:{seal}:{scope}:{endpoint}:{sha256(filter)}`; `scope` = `own:<owner>` hoặc `all`; owner KHÔNG nằm trong
  hash nên bỏ scope sẽ làm lộ dữ liệu (có test bắt). Luôn có TTL (`ANALYTICS_CACHE_TTL_S`).
- **Nhất quán giữa các kind.** Mỗi vòng đọc `canonical/summary.head_revision = H`, kéo mọi kind tới H, rồi áp MỘT transaction cùng `processed_events`,
  `feed_cursor` và seal. Không có trạng thái "nửa snapshot".
- **Đồng bộ theo revision, không chỉ theo event.** Cuối mỗi vòng so `head_revision` với con trỏ `canonical.*` nên mất event vẫn đuổi kịp.
- **Upsert chốt revision.** `ON CONFLICT ... WHERE stored.revision <= EXCLUDED.revision`: replay idempotent, bản cũ đến muộn không làm số lùi.
- **Quy tắc dữ liệu hiển thị.** Theo HÌNH DẠNG dữ liệu, không theo tên nền tảng: *bài đăng* (content) cộng `views` vào NGÀY ĐĂNG; *số liệu theo ngày*
  (daily metrics) cộng vào ngày đó. `null ≠ 0`. `videos_posted` = event `post_ok`. Quốc gia: override nhóm > resource > project, rồi alias → tên chuẩn; rỗng = "Khác".
  Kênh `removed`/bài `deleted` loại khỏi mọi số liệu nhưng giữ lịch sử.
- **Bộ lọc `country` không thu hẹp biểu đồ so sánh quốc gia** (`views-by-country`, `country-tags`, `compare/country`) — response có `ignored_filters:["country"]`.
- **Kênh mồ côi.** Hàng số liệu của kênh chưa có resource vẫn được tính khi bộ lọc chỉ gồm owner/nền tảng/quốc gia "Khác" (như legacy); mọi bộ lọc cần thông tin kênh thì loại chúng.

## Khác biệt có chủ đích so với legacy

1. `summary.managed_channels/active_channels` đếm MỌI kênh quản lý (kể cả YouTube) để khớp tổng của `channel-breakdown`; legacy chỉ đếm kênh có `repost_pairings`.
2. Bộ lọc `country`/`platform`/`project`/`group` áp cho CẢ KPI phụ (`videos_posted`, managed, active), vì KPI và biểu đồ dùng chung một FilterContext.
3. Event `post_ok` của kênh nền tảng nào cũng được tính vào `videos` (fallback) khi lọc theo nền tảng đó.
4. Response thêm `filter`, `freshness` (và `ignored_filters` ở biểu đồ quốc gia). `GET /v1/analytics/*` nhận `date_from`/`date_to` tuỳ chọn (mặc định 30 ngày).
5. Khoảng ngày sai định dạng → 422 (legacy: lỗi 500).
6. Không seed alias quốc gia mặc định (legacy seed một lần ở DB rỗng); alias/tên chuẩn hiện tại phải được admin nhập hoặc di chuyển từ DB cũ.

## Hợp đồng đối soát (Integration phải tuân theo)

`GET /internal/v1/canonical/summary` → `{head_revision, counts{kind:{total,active}}, checksum{kind: sha256hex}}` với `active` = số bản ghi `removed=false`
và `checksum[kind]` = sha256 (UTF-8) của các dòng đã **sort theo chuỗi** nối bằng LF, mỗi dòng:

```text
<khoá tự nhiên>|<revision>|<0/1 removed>[|<số liệu hiển thị>]
projects : owner|project_key
resources: owner|platform|resource_type|external_id
content  : owner|platform|resource_type|resource_external_id|external_id   + "|<views>,<reactions>,<comments>"   (null viết là n)
metrics  : owner|platform|resource_type|resource_external_id|YYYY-MM-DD    + "|<views>"
events   : owner|platform|resource_type|resource_external_id|external_id
```

Phân trang `canonical/{kind}`: một trang KHÔNG được cắt ngang một revision (trang có thể dài hơn `limit` để trọn revision cuối), vì con trỏ là `after_revision`.

## Cấu hình (`.env.example`)

Bắt buộc để phục vụ: `ANALYTICS_DATABASE_URL`, `ANALYTICS_ISSUER` (fail-fast nếu thiếu). Tuỳ chọn: `ANALYTICS_REDIS_URL`, `ANALYTICS_INTEGRATION_URL/_FEED_KEY`,
`ANALYTICS_IDENTITY_URL/_FEED_KEY` (thiếu ⇒ consumer tương ứng tắt), `ANALYTICS_IDENTITY_JWKS_URL`, `ANALYTICS_AUDIENCE`, `ANALYTICS_STALE_AFTER_S`, ...

## Chạy

```bash
cd services/analytics-service
python -m analytics_service.migrate                                   # job migration riêng
python -m uvicorn analytics_service.main:app --host 127.0.0.1 --port 8432
python -m pytest -q -p no:cacheprovider                               # cần Postgres + Redis dev (infra/scripts/devenv.py)
```

DB chết ⇒ tiến trình vẫn sống, `/health/ready` = 503, API trả 503 nhanh. `/health/ready` KHÔNG phụ thuộc Redis hay nguồn feed.

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
