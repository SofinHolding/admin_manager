# Ingest Data Contract v1 (contract_version `2026-10-01`)

Trạng thái: **ĐỀ XUẤT ĐÃ ĐÓNG BĂNG ĐỂ REVIEW** (Phase 0). Chưa có service nào triển khai.
Schema máy đọc được: [`packages/contracts/ingest/2026-10-01/ingest.schema.json`](../../packages/contracts/ingest/2026-10-01/ingest.schema.json).
Ví dụ đã được kiểm chứng với schema: thư mục `examples/` cùng nơi.

Tài liệu này trả lời D10: Tool đẩy dữ liệu gì, theo cấu trúc nào, và Manager bảo đảm gì để giao diện luôn
hiển thị **đúng dữ liệu thực tế và mới nhất**.

---

## 1. Dữ liệu Dashboard thực sự cần (kiểm kê từ code hiện tại)

Nguồn: `admin-server/stats_core.py` (docstring "Hình dạng input chuẩn hoá"), `admin-server/db.py:1722-1850`,
`seed_qa_demo.py`. Chỉ **7 store** nuôi toàn bộ Dashboard. Mọi store khác của Tool không được Manager dùng.

| Dashboard hiển thị | Dữ liệu đầu vào | Store hiện tại | Trường được đọc |
|---|---|---|---|
| Lượt xem Facebook theo ngày, tương tác, bình luận | từng bài đăng | `fb_posts` (khóa `<target>\|<post_id>`) | `target`, `date`, `views`, `reactions`, `comments_count` |
| Lượt xem YouTube theo ngày | số liệu kênh theo ngày | `yt_insights_daily` (khóa `YYYY-MM-DD\|channel_id`) | `channel_id`, `date`, `views` |
| Số video đăng | nhật ký đăng bài | `repost_events` | `kind` (`post_ok`), `date`, `target` |
| Kênh quản lý / đang hoạt động | cặp reup | `repost_pairings` | `target`, `enabled`, `group_id` |
| Nhóm chủ đề, quốc gia theo nhóm | nhóm reup | `repost_groups` | `id`, `name`, `enabled`, `country` |
| Tên và quốc gia của page Facebook | meta page | `fb_page_meta` | `target`, `name`, `country` |
| Tên và quốc gia của kênh YouTube | meta kênh | `yt_channels` | `id`, `title`, `country` |

**Ngữ nghĩa phải giữ nguyên** (từ `stats_core.py`):
- Lượt xem Facebook của một ngày = tổng `views` của các bài có **ngày đăng** là ngày đó. `views` của bài là
  số **cộng dồn hiện tại**, nên khi bài cũ có thêm lượt xem, số của **ngày cũ** phải đổi theo.
- `null` = chưa có dữ liệu, `0` = có dữ liệu và bằng 0. Không được ép `null` thành `0`.
- Kênh quản lý Facebook đếm theo cặp (target, chủ đề). Một page chạy hai chủ đề được tính là hai kênh.

## 2. Nguyên tắc để dữ liệu luôn mới và đúng

Vấn đề cần tránh: Tool chỉ gửi bản ghi **chưa từng gửi**, còn bản ghi cũ đã đổi số thì không được cập nhật.
Hợp đồng chặn việc này bằng các quy tắc bắt buộc sau.

| # | Quy tắc | Hệ quả |
|---|---|---|
| R1 | **Upsert theo khóa tự nhiên**, không phải "chèn nếu chưa có" | Bản ghi đã tồn tại luôn có thể được cập nhật |
| R2 | Mỗi bản ghi mang `observed_at`. **Bản có `observed_at` mới hơn thắng.** Bản cũ hơn bị bỏ qua và được đếm vào `stale_ignored` | Gửi lại muộn hoặc lệch thứ tự không làm số liệu lùi |
| R3 | Giá trị metric là **số tuyệt đối** tại `observed_at`, không phải số gia. Server ghi đè, **không bao giờ cộng dồn** | Gửi lại cùng bản ghi không làm số nhân đôi |
| R4 | Tool **phải gửi lại mọi bản ghi có metric đã đổi** kể từ lần commit trước. Khuyến nghị: mỗi chu kỳ gửi lại toàn bộ bài trong cửa sổ còn biến động (ví dụ 30 ngày gần nhất) | Bài cũ vẫn được cập nhật số mới |
| R5 | Snapshot `full` khai báo `scope`: "tập này ĐẦY ĐỦ cho phạm vi X". Bản ghi thuộc phạm vi nhưng **vắng mặt** sẽ bị đánh dấu `removed` sau commit (lịch sử giữ lại, Dashboard loại ra) | Bài hoặc kênh đã xóa ở Tool không nằm lại mãi trên Dashboard |
| R6 | `expected_counts` được kiểm tra lúc commit. Lệch thì `SNAPSHOT_INCOMPLETE`, **không commit** | Tool lỗi giữa chừng không làm Dashboard thiếu dữ liệu hay tụt về 0 |
| R7 | **Không bỏ sót im lặng.** Bản ghi sai bị trả về trong `rejections[]` kèm `code`. Mặc định (`accept_partial=false`) còn bản ghi bị từ chối thì không commit | Số liệu hiển thị không bao giờ là "một phần" mà người dùng không biết |
| R8 | Snapshot cũ hơn snapshot đã commit cùng phạm vi bị `SNAPSHOT_SUPERSEDED` ở phần suy luận vắng mặt (R5); từng bản ghi vẫn theo R2 | Snapshot đến muộn không xóa nhầm dữ liệu mới |
| R9 | Ngày (`YYYY-MM-DD`) hiểu theo `report_timezone` của snapshot. Đổi múi giờ giữa các snapshot của cùng Tool bị từ chối | Ranh giới ngày không trôi |
| R10 | Mọi mutation có `Idempotency-Key`. Gửi lại do timeout trả đúng kết quả cũ | Retry an toàn |

Phía Manager, để giao diện không phản ánh dữ liệu cũ:
- Dashboard **chỉ đọc giá trị mới nhất của snapshot đã commit**. Snapshot đang `building` hoặc `failed` không bao giờ hiện.
- Mỗi response analytics mang `freshness: { as_of, snapshot_id, committed_at, lag_seconds }`. UI hiển thị và
  cảnh báo "dữ liệu cũ" khi `as_of` trễ quá ngưỡng cấu hình.
- Cache được vô hiệu theo **seal phiên bản của snapshot vừa commit** (xem mục 2.10 ở assessment), không dựa vào
  cửa sổ TTL. Hiện `CACHE_VERSION_TTL=300` cho phép số liệu cũ tới 5 phút; thiết kế mới bỏ giới hạn đó.
- Một job đối soát định kỳ tính lại tổng từ kho canonical và so với read model. Lệch thì cảnh báo.

## 3. Luồng gửi dữ liệu

Dùng đúng namespace chung, không endpoint riêng theo Tool:

```text
POST /v1/integrations/snapshots                      → tạo snapshot (status=building)
POST /v1/integrations/snapshots/{id}/resources       → kênh/page + liên kết chủ đề
POST /v1/integrations/snapshots/{id}/content         → bài đăng/video + metric cộng dồn
POST /v1/integrations/snapshots/{id}/metrics         → metric theo ngày của kênh
POST /v1/integrations/snapshots/{id}/events          → sự kiện (đăng bài thành công, ...)
POST /v1/integrations/snapshots/{id}/commit          → commit atomic
PUT  /v1/integrations/projects/{project_key}         → chủ đề/nhóm (đã có trong contract trước)
```

- `/content` và `/events` là hai endpoint **bổ sung** vào contract `api-first-integration-contract-v1`. Hai
  endpoint cũ `/resources` và `/metrics` giữ nguyên nghĩa, `/metrics` nhận metric theo ngày của kênh.
- Mỗi batch tối đa **500** bản ghi (khớp trần `MAX_OPS` hiện tại). Hỗ trợ gzip.
- Ownership suy ra từ API key. Trường `owner_account_id` do Tool gửi **bị schema từ chối**.
- Tool có nhiều nền tảng thì tạo mỗi nền tảng một snapshot.

## 4. Thực thể và trường

Chi tiết kiểu dữ liệu: xem schema. Cột **Bắt buộc** là tập tối thiểu Dashboard cần.

### 4.1 Project (chủ đề/nhóm) — `PUT /projects/{project_key}`
Ánh xạ `repost_groups`. Body theo contract trước: `name`, `platform`, `country_code`, `metadata`. Bổ sung: `enabled` (bool).

### 4.2 Resource (kênh/page) — khóa `(platform, resource_type, external_id)`

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `external_id` | có | Facebook: id page (`target`). YouTube: `channel_id` |
| `resource_type` | có | `page`, `profile`, `channel` |
| `observed_at` | có | RFC 3339 |
| `display_name`, `username`, `url` | không | Tên hiển thị trên Dashboard |
| `country` | không | `{code}` (ISO-3166 alpha-2) và/hoặc `{name}` thô. Manager chuẩn hóa tên qua bảng alias quốc gia |
| `state` | không | `active` (mặc định) hoặc `disabled` |
| `projects[]` | không | `{project_key, enabled}`. Mỗi phần tử tương ứng một cặp reup (`repost_pairings`) |
| `managed`, `active` | không | Chỉ cần cho nền tảng **không có chủ đề** (như YouTube). Vắng thì suy ra: `managed` = có ≥ 1 liên kết chủ đề; `active` = có liên kết bật và chủ đề không tắt |

### 4.3 Content item (bài đăng/video) — khóa `(resource, external_id)`

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `external_id` | có | id bài ổn định. Hiện là `post_id` (khóa cũ `<target>\|<post_id>`) |
| `resource_external_id` | có | page/kênh sở hữu bài |
| `published_date` | có | Ngày đăng. **Quyết định ngày nào được cộng lượt xem** |
| `metrics` | có | `views`, `reactions`, `comments`, mỗi giá trị `integer ≥ 0` hoặc `null` |
| `observed_at` | có | Thời điểm đo metric |
| `state` | không | `active` hoặc `deleted` |
| `published_at`, `url` | không | |

### 4.4 Daily metric (số liệu theo ngày của kênh) — khóa `(resource, date)`
`resource_external_id`, `date`, `metrics{views,...}`, `observed_at`. Dùng cho YouTube. Ngày đã gửi có thể được sửa lại.

### 4.5 Activity event (sự kiện) — khóa `(external_id)`
`external_id`, `resource_external_id`, `kind`, `occurred_date`; tùy chọn `occurred_at`, `project_key`.
`kind = post_ok` nghĩa là một lần đăng thành công và được đếm vào "số video đăng". Sự kiện bất biến.

### 4.6 Metric Registry (dữ liệu, không phải code)
`views`, `reactions`, `comments` được dùng bởi Dashboard hiện tại. **`reactions` là khóa canonical mới** cần
thêm vào registry vì Dashboard đang dùng nó cho "tương tác" (contract trước chỉ có `likes`). Metric lạ nằm trong
`extensions.<platform>` cho tới khi được nâng lên registry.

## 5. Tool chỉ nên đẩy gì (giảm tối đa dữ liệu)

**Cần gửi** — đúng các trường ở mục 4, ứng với 7 store ở mục 1.

**Không gửi cho Manager** (Manager không dùng, một số là bí mật):

| Loại | Ví dụ |
|---|---|
| Bí mật | token OAuth, cookie phiên, `license_key`, `accounts.json` (đã cấm đồng bộ tuyệt đối) |
| Nội dung và tệp | file video, đường dẫn máy, tiêu đề hay mô tả bài nếu không cần hiển thị |
| Trạng thái nội bộ của Tool | `repost_videos`, `translate_sources/variants`, `projects`, hàng đợi, cấu hình local |
| Trường thừa | mọi trường của bản ghi ngoài danh sách mục 4 |

> Lưu ý quan trọng: kênh đồng bộ cũ (`/v1/push` tới sync server) còn phục vụ **đồng bộ nhiều thiết bị của
> chính Tool**. Việc đẩy theo hợp đồng này là **một kênh riêng** tới `/v1/integrations/*`. Đừng cắt bớt dữ
> liệu của kênh cũ cho tới khi Tool tự không còn cần nó.

## 6. Mã lỗi

Dùng bộ mã trong contract trước (`AUTH_INVALID`, `SCOPE_DENIED`, `IDEMPOTENCY_CONFLICT`, `SNAPSHOT_INCOMPLETE`,
`SNAPSHOT_ALREADY_COMMITTED`, ...). Bổ sung:

| Mã | Khi nào |
|---|---|
| `RECORD_VALIDATION_FAILED` | Sai kiểu, thiếu trường bắt buộc, ngày sai định dạng, metric âm hoặc không nguyên |
| `RESOURCE_UNKNOWN` | `resource_external_id` chưa có trong snapshot hay kho (gửi resource trước) |
| `METRIC_UNKNOWN` | Khóa metric chưa có trong registry (đưa vào `extensions`) |
| `SNAPSHOT_SUPERSEDED` | Snapshot cũ hơn snapshot đã commit cùng phạm vi, bỏ phần suy luận vắng mặt |
| `TIMEZONE_CHANGED` | `report_timezone` khác snapshot trước của cùng Tool |
| `BATCH_TOO_LARGE` | Quá 500 bản ghi |

## 7. Ánh xạ từ định dạng cũ (cho Legacy ACL)

Trong giai đoạn chuyển tiếp, Tool cũ vẫn ghi `documents`. Lớp canonicalizer của Integration dịch như sau:

| Store cũ | → Thực thể canonical |
|---|---|
| `fb_page_meta` | Resource `page` (`target`→`external_id`, `name`→`display_name`, `country`→`country.name`) |
| `yt_channels` | Resource `channel` (`id`, `title`, `country`) |
| `repost_groups` | Project (`id`→`project_key`, `name`, `enabled`, `country`) |
| `repost_pairings` | Liên kết `projects[]` của Resource (`target`, `group_id`, `enabled`) |
| `fb_posts` | Content item (`<target>\|<post_id>`; `date`→`published_date`, `comments_count`→`comments`) |
| `yt_insights_daily` | Daily metric (`channel_id`, `date`, `views`) |
| `repost_events` | Activity event (`id`, `kind`, `date`→`occurred_date`, `target`) |

Điểm khác của đường cũ: `documents.rev` tăng khi ghi, nhưng **không có `observed_at`**. Canonicalizer dùng
thời điểm nhận (`received_at`) làm `observed_at` thay thế cho tới khi Tool chuyển sang hợp đồng mới.

## 8. Kiểm thử chấp nhận ("giao diện đúng dữ liệu thực")

| # | Kịch bản | Kết quả đúng |
|---|---|---|
| T1 | Đẩy N bài với `views` biết trước | Tổng trên Dashboard bằng đúng tổng đã đẩy, theo từng ngày đăng |
| T2 | **Đẩy lại bài cũ với `views` lớn hơn** | Số của ngày đăng cũ tăng theo. (Ca chặn lỗi "chỉ chèn bản ghi mới") |
| T3 | Gửi lại bản có `observed_at` cũ hơn | Bị bỏ qua (`stale_ignored`), số không lùi |
| T4 | Snapshot `full` vắng một bài trong cửa sổ | Bài đó biến mất khỏi Dashboard, lịch sử còn |
| T5 | Retry cùng `Idempotency-Key` | Không nhân đôi |
| T6 | Tool dừng giữa chừng trước commit | Dashboard giữ nguyên số của snapshot đã commit gần nhất |
| T7 | `views: null` so với `0` | Biểu đồ phân biệt "chưa có dữ liệu" và "bằng 0" |
| T8 | Một bản ghi sai trong batch | Có trong `rejections[]`, không commit khi `accept_partial=false` |
| T9 | Bài đăng sát nửa đêm ở hai múi giờ | Rơi đúng ngày theo `report_timezone` |
| T10 | Cùng fixture qua đường cũ (`documents` + `stats_core`) và đường canonical | Số liệu Dashboard **giống hệt** |
| T11 | Commit snapshot rồi mở Dashboard ngay | `freshness.as_of` bằng snapshot vừa commit, không dính cache cũ |

## 9. Chưa xác định (cần xác minh, không đoán)

- `fb_posts.views` hiện do Tool cập nhật theo chu kỳ nào, và Tool có gửi lại bài cũ khi số đổi hay không. Đây
  chính là nguồn của rủi ro "dữ liệu cũ"; cần đối chiếu mã Tool ở repo `upload-facebook`.
- Múi giờ mà Tool đang dùng khi sinh `date` của bài và của `yt_insights_daily`.
- Định nghĩa chính xác của `reactions` ở Facebook (tổng mọi loại cảm xúc hay chỉ lượt thích).
- Nguồn của `yt_insights_daily.country` (record có trường `country` nhưng nơi quyết định giá trị chưa rõ).
- Kích thước thực tế: số bài trong cửa sổ 30 ngày của một Tool, để chốt giới hạn kích thước snapshot.
