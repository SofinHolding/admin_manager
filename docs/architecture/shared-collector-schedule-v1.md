# Shared Collector — lịch thu thập, thời điểm đẩy và ngày báo cáo (v1)

**Trạng thái:** ĐÃ CHỐT (2026-10-17). Tool: `apps/local-fallback-tool`. Server: `integration-service` (`POST /jobs/enqueue`, `resource_ids` ở `/jobs/claim`).
Bổ sung cho `shared-collector-design-v1.md` (ủy quyền ingest) — tài liệu này trả lời: **khi nào đẩy, khi nào thu lại, ngày tính theo giờ nào**.

## 1. Quyết định

| # | Câu hỏi | Quyết định | Lý do |
|---|---|---|---|
| D1 | Khi nào đẩy số liệu lên website? | **Ngay sau khi xong từng link** (một job = một link = một snapshot `delta`) | Ủy quyền ingest buộc snapshot gắn một job đang giữ lease (≤ 5 phút) nên không giữ dữ liệu tới một mốc giờ; lỗi cô lập từng link; tắt tool giữa chừng không mất phần đã thu. Số liệu ghi đè theo `(kênh, video)` và `(kênh, ngày)` (R1–R3) nên đẩy nhiều lần không làm nhân đôi. |
| D2 | Lịch "Hẹn giờ tự động" điều khiển gì? | **Khi nào collector chạy** (không điều khiển việc đẩy). Mốc giờ tính theo **giờ Việt Nam**, không theo múi giờ của máy | Người vận hành đặt lịch một lần, tool tự chạy hằng ngày. |
| D3 | Ai tạo việc thu thập lặp lại mỗi ngày? | **Tool**: mỗi đợt xin server tạo job cho các kênh **đã kéo** (`POST /jobs/enqueue`) | Server chỉ tự tạo MỘT job khi user thêm kênh, xong là hết; tool không tự tạo job được (chỉ push được khi đang giữ lease một job). |
| D4 | Ngày tính theo múi giờ nào? | `report_timezone = Asia/Ho_Chi_Minh` **cố định** (`system-endpoints.json`) | Dashboard lọc theo ngày local của người dùng Việt Nam. Server từ chối đổi múi giờ giữa các snapshot của cùng Tool (`TIMEZONE_CHANGED`) nên không cho cấu hình riêng từng máy. |
| D5 | Tool chạy kênh nào? | **Chỉ kênh đã kéo và đang bật** (tab Người dùng & Kênh) | `enqueue` và `claim` đều lọc theo danh sách kênh đã kéo (`resource_ids`). |

## 2. Một đợt thu thập

```
mốc giờ (giờ VN) ── tool bật collector (hoặc người vận hành bấm Bắt đầu)
  └─ handshake ─ danh sách nền tảng được phép
  └─ vòng lặp:
       kênh đã kéo & bật  ──(mỗi kênh MỘT lần mỗi ngày VN)──▶ POST /jobs/enqueue
       POST /jobs/claim { resource_ids: <kênh đã kéo> }
       mỗi job: thu thập link ─▶ snapshot delta + X-Delegated-Job ─▶ commit ─▶ complete   ← đẩy NGAY
hết "Chạy trong N phút" ── collector dừng; job chưa kịp xử lý hết lease / được thu ở mốc kế tiếp
```

- **Mỗi lần khởi động** collector xin job cho mọi kênh đã kéo (mỗi mốc giờ của lịch = một đợt). Collector chạy xuyên đêm thì **sang ngày mới (giờ VN)** xin lại một lần. Kênh kéo thêm giữa phiên được xin riêng ngay vòng kế tiếp.
- `enqueue` idempotent: kênh đang có job pending/leased không bị tạo thêm (`already_active`); job đã xong thì lần đợt sau tạo job mới.
- Không có kênh nào đã kéo (hoặc đều tắt) → collector chờ, **không claim** job nào.
- Mốc giờ trong lịch (`HH:MM`) và `Chạy trong N phút` hiểu theo giờ Việt Nam.

## 3. Ngày hiển thị trên dashboard — vì sao đúng

Dashboard tính theo ngày từ hai nguồn (`analytics-service/readmodels/aggregate.py`):

| Số liệu | Ngày dùng | Tool gửi |
|---|---|---|
| Lượt xem theo ngày của **video** | `published_date` của bài; `views` là số cộng dồn hiện tại ⇒ thu lại làm đổi số của **ngày đăng** cũ | `content[].published_date` (giờ VN) |
| Chỉ số **kênh** (followers, videos_posted, likes) | `(kênh, date)`, ghi đè trong ngày — lần đẩy cuối của ngày thắng | `metrics[].date` = ngày đo theo giờ VN |
| Số video đăng | số bài theo `published_date` | như trên |

Hệ quả: đẩy sớm hay muộn trong ngày **không làm đổi ngày gán**; thu lại chỉ cập nhật số mới nhất của đúng ngày đăng. `observed_at` luôn là khoảnh khắc đo (UTC, tuyệt đối): bản mới hơn thắng (R2), đẩy lại muộn không làm số lùi.

### Quy đổi mốc thời gian của nguồn → ngày giờ Việt Nam (`system_api.to_report_date`)

| Giá trị nguồn trả | Cách xử lý | Ví dụ |
|---|---|---|
| Epoch giây / mili-giây, hoặc chuỗi số (vd TikTok `create_time`) | Mốc **tuyệt đối** ⇒ đổi thẳng sang giờ VN; `region` không ảnh hưởng | `1792170000` ⇒ `2026-10-17` (00:00 VN) |
| ISO có `Z` / offset | Tuyệt đối ⇒ đổi sang giờ VN | `2026-10-17T00:30:00+09:00` ⇒ `2026-10-16` (22:30 VN) |
| ISO **không** offset (giờ địa phương) | Dùng múi giờ của `region` nếu quốc gia chỉ có **một** múi giờ (bảng `REGION_TZ`), rồi đổi sang giờ VN; không rõ / nhiều múi giờ ⇒ coi là UTC (không đoán) | `2026-10-17 00:30:00` + `KR` ⇒ `2026-10-16` |
| Chỉ có ngày `YYYY-MM-DD` | Giữ nguyên (không có giờ để quy đổi) | `2026-10-17` |
| Không đọc được | Ngày của thời điểm đo theo giờ VN | — |

Mẫu TikTok có `region` (`"KR"`) và `create_time` (epoch): ngày VN suy ra từ epoch; `region` được lưu vào `extensions.tiktok.region` của bài để hiển thị/tra cứu.

Máy Windows không có cơ sở dữ liệu múi giờ (thiếu gói `tzdata`): tool dùng UTC+7 cố định cho Việt Nam (không có giờ mùa hè).

## 4. Hợp đồng server liên quan

- `POST /v1/integrations/jobs/enqueue` — scope `jobs:claim:system`, `client_kind=fallback_tool`; body `{resource_ids:[1..200]}`; trả `{created, already_active, skipped:[{resource_id, reason}]}`; owner dữ liệu của job = chủ kênh (delegated ingestion); `dedupe_key = collect:<resource_id>`; tôn trọng hạn mức job đang chờ của owner (`LIMIT_EXCEEDED` chỉ chặn kênh đó).
- `POST /v1/integrations/jobs/claim` — thêm `resource_ids` (tuỳ chọn, ≤500): chỉ nhận job của các kênh đó. Rỗng = không lọc (tương thích cũ).
- Cả hai thay đổi chỉ **thêm**, tương thích ngược; OpenAPI đóng băng đã sinh lại.

## 5. Chưa làm (ngoài đợt này)

- TikTok: liệt kê video bằng yt-dlp không cookie, lặp tối đa 25 lượt qua cổng chống bot (`tiktok_links.py`); chặn hết 25 lượt thì job fail kèm gợi ý (không đẩy 0 giả). Quy đổi ngày/`region` dùng chung cho mọi nền tảng.
- Mỗi lần mới thu: YouTube 30 video gần nhất (yt-dlp: 10), TikTok 15, Facebook một link; R4 khuyến nghị gửi lại toàn bộ bài trong cửa sổ còn biến động (≈30 ngày).
- Cấu hình thu thật: YouTube API key (nhiều key), proxy (nhiều proxy), cookie Facebook — ở Cấu hình của tool (`.dev/collector-config.json`).
