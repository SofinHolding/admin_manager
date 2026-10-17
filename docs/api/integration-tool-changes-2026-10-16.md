# Thay đổi API tích hợp dành cho Tool đã liên kết (2026-10-16)

**Đối tượng:** chủ/nhà phát triển mọi Tool đẩy dữ liệu lên Manager — Tool cá nhân (credential `primary_tool`), Tool dùng chung (collector, `fallback_tool`) và Tool dùng `manager_sdk`.
**Phiên bản hợp đồng:** `X-Contract-Version: 2026-10-01` (không đổi; các Tool liên kết được sửa đồng bộ cùng đợt này — mục 7).
**Tài liệu gốc:** `docs/api/integration-api-v1.md` (§3), `docs/api/ingest-data-contract-v1.md` (§4.7 cho Tool dùng chung), `packages/contracts/ingest/2026-10-01/ingest.schema.json`.

## 1. Nguyên tắc thiết kế

### 1.1 Mã nền tảng
- Admin/dash_admin tạo sẵn danh mục nền tảng ở trang **Nền tảng** của website. Mỗi nền tảng (tên hiển thị) có **một mã chính** và **nhiều mã phụ**; mọi mã là **duy nhất toàn hệ thống**.
  Mã chính = tên chuẩn lưu trong hệ thống và hiện trên Dashboard; mã phụ = tên gọi khác để Tool gửi theo thói quen của mình.
- **Mọi dữ liệu Tool gửi lên PHẢI kèm một mã nền tảng hợp lệ** (mã chính hoặc mã phụ). Server chuẩn hoá về mã chính trước khi kiểm quyền và lưu ⇒ Dashboard gom đúng nền tảng.
- **Mã sai thì dữ liệu không được lưu ở đâu cả**: mã không có trong danh sách, mã phụ đã bị gỡ, nền tảng đã bị tắt, hoặc nền tảng ngoài phạm vi client ⇒ `403 PLATFORM_NOT_ALLOWED`; thiếu mã ⇒ `422`. Không tạo snapshot, không lưu kênh, không phát sự kiện.
- **Một snapshot = một nền tảng.** Tool có dữ liệu của nhiều nền tảng gửi **một snapshot cho mỗi nền tảng**, mỗi snapshot kèm mã nền tảng của dữ liệu đó.
- Danh sách mã được dùng lấy từ API (không hardcode): `GET /v1/integrations/platforms`. Người dùng xem cùng danh sách trên trang **Tích hợp Tool** → mục "Mã nền tảng dùng khi gửi dữ liệu" (chỉ xem, không sửa được); file `openapi.json` tải từ trang đó có `enum` mã hợp lệ ở mọi trường `platform`.

### 1.2 Không còn "project" ở phía Tool
- `PUT /v1/integrations/projects/{project_key}` **đã bị gỡ**. Phân loại/gom kênh hoặc người dùng do **Nhóm kênh** trên website đảm nhiệm; Tool không phải gửi gì thêm.
- Tool đang gọi endpoint này phải **ngừng gọi** (nhận `404`). Trường `projects[]` (resource, kể cả `project_key`/`enabled` tiện ích của `resources/batch`) và `project_key` (event) **cũng đã gỡ**: gửi vào bị từ chối (`RECORD_VALIDATION_FAILED`, bản ghi đó không lưu). Kênh đã lưu trước đây không bị đổi.
- Dashboard không đổi: không có chiều/ô lọc "project" trên giao diện. Tính năng project sẽ được thiết kế lại và bổ sung sau — lúc đó là thay đổi cộng thêm, có hướng dẫn riêng.

## 2. Bảng API thay đổi

| API | Thay đổi | Việc Tool phải làm |
|---|---|---|
| `GET /v1/integrations/platforms` | **MỚI.** Scope `integration:handshake`. Trả nền tảng + mã client được dùng | Gọi khi khởi động; chỉ gửi `platform` ∈ `codes` |
| `POST /v1/integrations/snapshots` | `platform` bắt buộc; **nhận mã phụ**; phản hồi `platform` = **mã chính** | Gửi mã trong `codes`. Không giả định phản hồi trả lại đúng mã đã gửi |
| `POST /snapshots/{id}/{content\|metrics\|events}` | Không đổi định dạng. Nền tảng lấy từ **snapshot chứa nó** | Mỗi snapshot chỉ đưa dữ liệu của đúng nền tảng đó |
| `POST /v1/integrations/resources/batch` | `platform` mỗi bản ghi bắt buộc; **nhận mã phụ**; lô trộn mã đúng + sai bị từ chối **cả lô** (403) | Tách lô theo nền tảng; dùng mã hợp lệ |
| `PUT /v1/integrations/projects/{key}` | **ĐÃ GỠ** (mục 1.2) | Ngừng gọi |
| `POST /v1/integrations/handshake` | `platforms[]` nhận mã phụ; phản hồi `allowed_platforms` là mã chính | Có thể để `platforms: []` (= mọi nền tảng được phép) |
| `POST /v1/integrations/jobs/claim` | `platforms[]` nhận mã phụ; `job.platform` là **mã chính** | Lọc theo mã chính/phụ đều được |
| `GET /v1/integrations/resources?platform=` | Bộ lọc nhận mã phụ | — |
| `GET /v1/integrations/me/connection-guide` | Thêm `platforms[]` (`key`, `display_name`, `aliases`, `codes`), `platform_rules[]`; mọi trường `platform`/`platforms` trong `request_schema` có `enum` = mã được dùng | Đọc để biết mã hợp lệ của từng client |
| Quản trị (không phải Tool): `POST /admin/platforms`, `…/aliases` | Tạo nền tảng/mã phụ, chặn trùng `409 PLATFORM_CODE_EXISTS` | Không dùng từ Tool |

### Mã lỗi liên quan
| HTTP | `error.code` | Khi nào | Dữ liệu |
|---|---|---|---|
| 403 | `PLATFORM_NOT_ALLOWED` | Mã sai / đã gỡ / nền tảng tắt / ngoài phạm vi client / (Tool dùng chung) khác `job.platform` | **Không lưu** |
| 422 | `RECORD_VALIDATION_FAILED` | Thiếu `platform` hoặc sai định dạng `^[a-z][a-z0-9_]{0,63}$` | **Không lưu** |
| `rejections[]` | `RESOURCE_UNKNOWN` | Bản ghi `content/metrics/events` trỏ kênh không có ở **nền tảng của snapshot** | Bản ghi đó bị bỏ, báo trong `rejections` |

## 3. Định dạng: trước → sau

### 3.1 Lấy danh sách mã (MỚI)
```http
GET /v1/integrations/platforms
Authorization: Bearer mpk_…
X-Installation-Id: ins_…
```
```json
{ "ok": true, "data": [
  { "key": "tiktok", "display_name": "TikTok", "enabled": true, "description": null,
    "aliases": ["tt"], "codes": ["tiktok", "tt"] },
  { "key": "facebook", "display_name": "Facebook", "enabled": true, "description": null,
    "aliases": [], "codes": ["facebook"] } ] }
```

### 3.2 Tool có dữ liệu nhiều nền tảng — MỘT snapshot cho MỖI nền tảng
Trước (sai, dồn nhiều nền tảng vào một lượt/không kèm mã):
```json
POST /v1/integrations/snapshots   { "snapshot_type": "full", "observed_at": "…", "report_timezone": "UTC", "expected_counts": {…} }
```
Sau:
```json
POST /v1/integrations/snapshots   { "platform": "tt",       "snapshot_type": "delta", "observed_at": "2026-10-16T01:00:00Z", "report_timezone": "UTC", "expected_counts": { "content": 40 } }
POST /v1/integrations/snapshots   { "platform": "facebook", "snapshot_type": "delta", "observed_at": "2026-10-16T01:00:00Z", "report_timezone": "UTC", "expected_counts": { "content": 12 } }
```
Phản hồi snapshot thứ nhất: `"platform": "tiktok"` (đã chuẩn hoá). Sau đó gửi `content/metrics/events` vào đúng snapshot của nền tảng đó rồi `commit` từng snapshot.

Với SDK Python:
```python
await client.push_by_platform(
    {"tt": {"content": tiktok_posts}, "facebook": {"resources": fb_pages, "content": fb_posts}},
    observed_at=now, report_timezone="UTC", snapshot_type="delta")
```
(`IntegrationClient.put_project` đã bị gỡ cùng endpoint.)

### 3.3 `resources/batch` — tách lô theo nền tảng, kèm quốc gia
```json
POST /v1/integrations/resources/batch
{ "items": [ { "platform": "kwai_vn", "resource_type": "page", "external_id": "ch1",
               "country": { "code": "VN" }, "observed_at": "…" } ] }
```
Lô `[ {platform:"facebook"…}, {platform:"khong_co"…} ]` ⇒ `403 PLATFORM_NOT_ALLOWED`, **cả hai bản ghi đều không được lưu**.

## 4. Tool dùng chung (collector, `fallback_tool`)

### 4.1 Mô hình
User nhập danh sách kênh trên website (chọn **một nền tảng + một quốc gia**, dán nhiều link, mỗi dòng một link) → mỗi kênh thành một **job** → Tool dùng chung claim job → kéo thông tin kênh → lấy danh sách video → gửi lên
**một snapshot `delta` cho kênh đó**. Collector không tự chọn nền tảng/chủ sở hữu: tất cả lấy từ **job đã claim** (`job.platform` đã là mã chính, `job.resource.external_id`). Mọi lệnh ghi gửi kèm `X-Delegated-Job: <job_id>`.

### 4.2 Cấu trúc dữ liệu gửi lên (đã thiết kế sẵn, dùng cho mọi nền tảng — kể cả TikTok khi có adapter)
| Dữ liệu | Endpoint | Khóa lưu | Trường |
|---|---|---|---|
| Chỉ số của **kênh** | `/snapshots/{id}/metrics` | `(kênh, ngày UTC)` → có lịch sử theo ngày | `resource_external_id` (= kênh của job), `date`, `metrics{followers, videos_posted, likes}`, `observed_at` |
| Từng **video** của kênh | `/snapshots/{id}/content` | `(kênh, external_id video)` → luôn là số mới nhất | `external_id`, `resource_external_id`, `published_date`, `url`, `metrics{views, reactions, comments, shares}`, `observed_at`, `extensions.<nền tảng>{title, duration_seconds, cover_url}` |

Ví dụ trọn vẹn cho một kênh TikTok:
```json
POST /v1/integrations/snapshots   (X-Delegated-Job: job_…)
{ "platform": "tiktok", "snapshot_type": "delta", "observed_at": "2026-10-16T01:00:00Z", "report_timezone": "UTC",
  "expected_counts": { "metrics": 1, "content": 2 } }

POST /v1/integrations/snapshots/{id}/metrics
{ "items": [ { "resource_external_id": "<job.resource.external_id>", "date": "2026-10-16",
               "metrics": { "followers": 1200, "videos_posted": 2, "likes": 55 }, "observed_at": "2026-10-16T01:00:00Z" } ] }

POST /v1/integrations/snapshots/{id}/content
{ "items": [
  { "external_id": "7301…", "resource_external_id": "<job.resource.external_id>", "published_date": "2026-10-04",
    "url": "https://www.tiktok.com/@kenh/video/7301…", "observed_at": "2026-10-16T01:00:00Z",
    "metrics": { "views": 100, "reactions": 10, "comments": 2, "shares": 3 },
    "extensions": { "tiktok": { "title": "Clip 1", "duration_seconds": 15 } } },
  { "external_id": "7302…", "resource_external_id": "<job.resource.external_id>", "published_date": "2026-10-05",
    "observed_at": "2026-10-16T01:00:00Z", "metrics": { "views": 5 } } ] }

POST /v1/integrations/snapshots/{id}/commit   {}
```
Quy tắc: số liệu là **tuyệt đối tại `observed_at`**, gửi lại thì ghi đè (không cộng dồn); `expected_counts` phải khớp số bản ghi từng loại, lệch ⇒ commit lỗi và **không lưu gì**; khóa metric nằm trong Metric Registry; mô tả video (tiêu đề…) vào `extensions.<nền tảng>`.
Hiện Dashboard vẫn chỉ đọc `views/reactions/comments` của video; chỉ số kênh mới (`followers`…) đã được lưu sẵn để hiển thị về sau.

### 4.3 Định dạng phải theo
| Bước | Định dạng đúng |
|---|---|
| Tạo snapshot | `platform` = `job.platform`; `snapshot_type: "delta"` (**chỉ `delta`**, `full` ⇒ 422); `Idempotency-Key: collect-<job_id>-<attempt>`. `platform` khác `job.platform` ⇒ 403 |
| Gửi số liệu | Theo 4.2; `resource_external_id` **phải đúng kênh của job** (khác ⇒ bị từ chối `RESOURCE_UNKNOWN`) |
| Không được | Gửi `/snapshots/{id}/resources` (⇒ 403 `SCOPE_DENIED`); đường `…/items` KHÔNG tồn tại |
| Commit | `POST /snapshots/{id}/commit` `{}` — thiếu số bản ghi so với `expected_counts` ⇒ 422, không lưu |
| Hoàn tất job | `POST /jobs/{id}/complete` **`{ "result": {…}, "lease_token": "<từ claim>" }`** (gửi không body ⇒ 422) |
| Báo lỗi job | `POST /jobs/{id}/fail` **`{ "error_code": "COLLECT_FAILED", "message": "…", "retryable": true, "lease_token": … }`** (trường cũ `reason` bị từ chối 422; `error_code` khớp `^[A-Za-z0-9_.:\-]{1,64}$`) |
| Gia hạn | `POST /jobs/{id}/heartbeat` `{ "lease_seconds": 300, "lease_token": … }` |
| Kiểm lỗi | **Mọi** phản hồi phải được kiểm (HTTP ≥ 400, `rejected > 0`) — lỗi ⇒ job `fail`, không báo hoàn tất giả |

Khởi động: nạp **file kết nối** Admin cấp cùng key (`connection-<client_id>.json`, `format: "manager-connection/1"`: `gateway`, `api_key`, `allowed_platforms`…) — `python collect.py --connection-file …`. Key cần scope `jobs:claim:system` để nhận job của mọi user.
Collector chỉ nhận job của nền tảng nó có adapter ∩ nền tảng key được phép. Adapter nằm trong `apps/local-fallback-tool/channel_collect.py` (YouTube Data API/yt-dlp, TikTok yt-dlp + tikwm, Facebook link bài/reel) và điền `items` (video) + `channel_stats` (`followers/videos_posted/likes`) cho `collect.process_job`; không lấy được số liệu hợp lệ nào ⇒ job `fail` kèm lý do thật, không ghi 0. Phần đẩy lên, định dạng và lưu đã có test.

## 5. Tool cá nhân (`primary_tool`)
Tool có sẵn danh sách link kênh theo **quốc gia + nền tảng** (Tool tự đăng ký kênh vào hệ thống):
- Tự được phép dùng **mọi nền tảng Admin tạo** (cập nhật tự động) — không cần xin thêm quyền.
- Với mỗi nền tảng: `POST /resources/batch` (mỗi kênh kèm `country`) → một snapshot của nền tảng đó, `content`/`metrics` theo cấu trúc mục 4.2, `commit`.
- Việc phải sửa: (1) gọi `GET /platforms` để biết mã; (2) mọi snapshot/`resources/batch` kèm mã hợp lệ; (3) tách snapshot theo nền tảng; (4) ngừng gọi `PUT /projects`; (5) bỏ giả định phản hồi trả đúng mã đã gửi.

## 6. Checklist sửa Tool
1. Bỏ danh sách nền tảng hardcode; nạp từ `GET /v1/integrations/platforms`.
2. Mỗi dữ liệu có nhãn nền tảng nguồn; gom theo nền tảng; mỗi nhóm một snapshot.
3. Bỏ mọi lời gọi `PUT /projects/{key}` và các trường `projects[]`/`project_key`.
4. Xử lý `403 PLATFORM_NOT_ALLOWED` = **cấu hình sai mã** (đừng retry nguyên lô; báo cho Admin kiểm danh mục mã).
5. (Collector) dùng đúng định dạng mục 4; kiểm mọi phản hồi.
6. Kiểm thử: gửi một mã phụ ⇒ kỳ vọng snapshot trả `platform` = mã chính; gửi mã lạ ⇒ kỳ vọng 403 và số kênh/snapshot không đổi.

## 7. Phiên bản và thay đổi gây lỗi
- Gây lỗi với Tool cũ: (a) `PUT /projects/{key}` không còn (404); (b) trường `projects[]`/`project_key` (resource, event) bị từ chối `RECORD_VALIDATION_FAILED`; (c) dữ liệu thiếu/sai mã nền tảng bị từ chối.
- Chính sách repo (`CLAUDE.md` §2.11) ưu tiên version hợp đồng mới cho thay đổi gây lỗi. Đợt này giữ `2026-10-01` vì toàn bộ Tool liên kết đang được sửa đồng bộ; nếu sau này có Tool bên ngoài chưa sửa kịp thì tạo version `2026-10-16` trước khi bật trên production.
- Ràng buộc ingest ủy quyền (mục 4.3) chỉ ảnh hưởng collector viết sai định dạng (vốn không đẩy được dữ liệu trước đó).
