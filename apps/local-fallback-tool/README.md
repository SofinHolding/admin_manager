# Local Fallback Tool (Tool B) — Electron desktop app

Tool B chạy dưới dạng ứng dụng desktop Electron, quản lý trực tiếp subprocess `collect.py`
(thu thập dữ liệu kênh YouTube/TikTok/Facebook cho Shared Collector) trên máy vận hành viên —
không cần đăng nhập, không gọi `admin-server`.

## Kiến trúc

- `electron/main.cjs` — main process: tạo cửa sổ, spawn/kill `collect.py`, đọc/ghi
  `.dev/collector-config.json`, đọc tail `.dev/collector.log` (cả hai nằm ở gốc monorepo).
- `electron/preload.cjs` — expose `window.collector` (contextIsolation bật, không
  `nodeIntegration`) cho renderer gọi qua IPC.
- `src/pages/CollectorPage.tsx` — UI duy nhất: status dot, form cấu hình, start/stop, log panel
  polling 2 giây.
- `src/electron.d.ts` — khai báo kiểu `Window.collector` dùng trong renderer.
- `system-endpoints.json` — NGUỒN SỰ THẬT về địa chỉ máy chủ nhúng sẵn (`default_gateway`) và mọi endpoint hệ thống mà tool dùng.
- `system_api.py` — `SystemApi`: client duy nhất gọi hệ thống (collect.py và kiểm tra kết nối đều đi qua đây) + `probe`.
- `src/pages/SystemApiPage.tsx` — tab "API hệ thống": liệt kê endpoint đã ánh xạ, nút "Kiểm tra kết nối".

## Chạy dev

```powershell
npm install
npm run electron:dev   # vite (cổng 5176) + electron song song, cửa sổ load http://localhost:5176
```

## Build

```powershell
npm run electron:build   # vite build (base: './') rồi electron-builder đóng gói portable .exe
```

`collect.py` và các module Python đi kèm được đóng gói (`build.files` trong `package.json`); máy chạy ứng dụng cần Python trong PATH và đã
`pip install -r requirements.txt` (httpx[socks], curl_cffi, yt-dlp, beautifulsoup4).

## Địa chỉ máy chủ nhúng sẵn và ánh xạ endpoint

Tool mang sẵn địa chỉ hệ thống hiện tại `http://192.168.1.70:8480` (`default_gateway` trong `system-endpoints.json`; chỉ dùng được khi máy
chạy tool cùng mạng LAN với máy chủ). Không cần nhập Gateway URL: chỉ cần **API key** (xin ở Admin → Công cụ dùng chung, key bind 1 máy). Nạp
file kết nối hoặc nhập tay Gateway URL thì địa chỉ đó thắng địa chỉ nhúng sẵn. Đổi địa chỉ nhúng sẵn = sửa MỘT chỗ là `system-endpoints.json`.

Mọi endpoint mặt phẳng MÁY của `integration-service` đã được ánh xạ trong `system-endpoints.json` (handshake, platforms, resources,
resource-owners, snapshots + resources/content/events/metrics/commit/status, jobs claim/heartbeat/complete/fail, workers register/heartbeat,
resources/batch) cộng `GET /v1/health`. `tests/test_system_api.py` so danh sách này với `guide/catalog.py` của server: server thêm/đổi/bỏ endpoint
mà tool chưa theo kịp thì test đỏ.

Tab **API hệ thống** (hoặc CLI `echo '{"api_key":"mpk_..."}' | python system_api.py probe`) chạy thử từng endpoint an toàn (đọc / idempotent)
và báo OK/Lỗi/Bỏ qua từng cái; endpoint ghi hoặc đổi trạng thái job không bao giờ bị chạy thử.

## Kéo kênh từ website: chọn từng kênh, kéo rồi giữ kết nối

Tab **Người dùng & Kênh** chỉ làm việc với user/kênh có `execution_mode = fallback_tool` (khai báo trên web, chế độ "Máy tập trung"). User có
Tool riêng đẩy kênh bằng `primary_tool` nên KHÔNG hiện ở đây; Fetch báo rõ số user/kênh bị bỏ qua. User vừa có Tool riêng vừa khai báo kênh
trên web thì chỉ các kênh `fallback_tool` của họ xuất hiện. Lọc nằm ở `system_api.shared_view` (một lần gọi `GET /resources`).

Mỗi kênh có hai trạng thái, lưu trong DB cục bộ (`channels_db.py`, cột `pulled`/`pulled_at`) nên đổi tab hay khởi động lại vẫn giữ:

- **Chưa kéo** — kênh hiện có trên website. **Fetch** làm tươi danh sách (kênh mới vào ở trạng thái này; thông tin hiển thị được cập nhật;
  user/kênh không còn thuộc tool dùng chung bị dọn).
- **Đã kéo** — tích chọn một hoặc nhiều kênh (hoặc cả nhóm, hoặc "Chọn tất cả chưa kéo") rồi **Kéo N kênh đã chọn**. Kênh đã kéo KHÔNG
  kéo lại được (nút chỉ sáng khi trong lựa chọn có kênh chưa kéo); tool giữ kết nối với kênh để đẩy dữ liệu lên. Với kênh đã kéo có thể
  Bật/Tắt cục bộ hoặc **Gỡ kết nối** (về lại chưa kéo). Fetch không đè trạng thái đã kéo lẫn cờ Bật/Tắt cục bộ.

## Lịch thu thập, thời điểm đẩy và ngày báo cáo

Thiết kế đầy đủ: `docs/architecture/shared-collector-schedule-v1.md`. Tóm tắt:

- **Đẩy ngay từng link**: xong link nào đẩy lên website link đó (một job = một snapshot `delta`), không chờ hết đợt. Lịch **"Hẹn giờ tự động"** chỉ quyết
  định khi nào collector chạy; mốc giờ và "Chạy trong N phút" tính theo **giờ Việt Nam**, không theo múi giờ của máy.
- **Thu lại hằng ngày**: mỗi lần collector chạy (mỗi mốc giờ, hoặc khi bấm Bắt đầu) và mỗi khi sang ngày mới nếu chạy xuyên đêm, tool gọi
  `POST /jobs/enqueue` cho các kênh **đã kéo và đang bật** (mỗi kênh một lần mỗi ngày), rồi chỉ nhận job của các kênh đó (`resource_ids`).
  Không có kênh đã kéo thì collector chờ, không nhận job nào. Server chỉ tự tạo một job khi user thêm kênh nên không có bước này thì kênh chỉ thu một lần.
- **Ngày theo giờ Việt Nam**: `report_timezone = Asia/Ho_Chi_Minh` cố định trong `system-endpoints.json`. `published_date` của bài và `date` của chỉ số kênh được
  quy đổi theo giờ VN (`system_api.to_report_date`): epoch / ISO có offset đổi thẳng; giờ địa phương không offset dùng múi giờ của `region` nếu quốc gia
  chỉ có một múi giờ (không thì coi là UTC). `region` của nguồn (vd TikTok `region: "KR"`) được lưu vào `extensions.<nền tảng>.region`.


## Kết nối bằng file Admin cấp

Khi Admin cấp key cho công cụ dùng chung (trang Admin → Công cụ dùng chung), giao diện cho tải
`connection-<client_id>.json` (`format: "manager-connection/1"`, chứa `gateway` + `api_key`; **là secret**, chỉ có lúc cấp/xoay).

- CLI: `python collect.py --connection-file connection-<client_id>.json [--settings-file .dev/collector-config.json]`
- Electron: tab Thu thập → Cấu hình → **Nạp file kết nối…** (tự điền Gateway URL + API Key).

Collector hỏi server `GET /v1/integrations/platforms` để biết nền tảng key được dùng, chỉ xử lý giao với nền tảng có adapter
(`youtube`, `tiktok`, `facebook`). Mỗi job đẩy lên **đúng kênh + nền tảng của chính job** (`resource.external_id`, `job.platform`)
bằng snapshot `delta` có `expected_counts`; mọi bước được kiểm lỗi (không thành công giả). Key cần scope `jobs:claim:system` để nhận job của mọi user.
Khoá YouTube, proxy, cookie Facebook được collector đọc từ file cấu hình (`--settings-file`), KHÔNG truyền qua dòng lệnh.

Định dạng payload collector phải gửi (snapshot delta, job complete/fail/heartbeat, ràng buộc `X-Delegated-Job`): `docs/api/integration-tool-changes-2026-10-16.md` §4.

## Bộ kiểm tra theo nền tảng (chuyển từ CheckviewV2)

`channel_collect.py` thu số liệu một kênh; các bộ kiểm tra nằm trong `checkers/` (không còn gọi subprocess `check_*.py` của CheckviewV2):

| Nền tảng | Cách làm |
|---|---|
| YouTube | YouTube Data API v3, xoay **nhiều key** (key hết quota nghỉ 6 giờ, dùng key kế); không có key thì yt-dlp. Lấy 30 video mới nhất + `followers`/`videos_posted` của kênh. |
| TikTok | Liệt kê video mới nhất bằng yt-dlp lặp qua cổng chống bot (`tiktok_links.py`, xem mục dưới), số liệu từng video (views/likes/comments/shares/region/ngày) qua tikwm `/api/` (tự thử lại lỗi tạm thời). |
| Facebook | Chỉ link **một bài/reel/video** (cookie Facebook, `checkers/facebook_scraper` chép từ CheckviewV2 commit `74026c1`, xem `NOTICE.md`). Trang/profile không liệt kê được => job fail kèm lý do, không thử lại. |

Proxy: danh sách nhiều proxy xoay vòng (`channel_collect._with_proxy`); lỗi do proxy/mạng thì proxy đó nghỉ 5 phút và dùng proxy kế, lỗi của riêng video thì giữ nguyên proxy.

## Cấu hình nhiều YouTube key / nhiều proxy

Tab Thu thập → Cấu hình: mỗi ô nhận **nhiều dòng, mỗi dòng một mục** (key `AIza…`; proxy dạng `scheme://user:pass@host:port`, `user:pass@host:port`,
`host:port`, `host:port:user:pass`; scheme `http|https|socks4|socks5|socks5h`). Phân tích + bỏ trùng chạy ở Python (`pools.py`) để cùng quy tắc với collector;
dòng không hợp lệ ở lại ô nhập để sửa. Mỗi dòng trong danh sách có trạng thái (chưa kiểm tra / sống / hết quota / chết / chưa rõ), nút **Kiểm tra tất cả / đã chọn**
(`health.py`: key dò bằng `videos.list part=id` = 1 đơn vị quota; proxy đi qua ipify/Cloudflare trace và hiện IP ra), **✕** xoá từng mục, **Xóa đã chọn**, **Xóa mục chết**.
Danh sách chỉ đổi trong cấu hình đang soạn đến khi bấm **Lưu cấu hình**. Key hiển thị đã che (`AIzaAB…WXYZ`), mật khẩu proxy hiển thị `***`; lỗi không bao giờ chứa key/mật khẩu.
Cấu hình cũ một giá trị (`youtube_api_key`, `proxy`) được gộp vào danh sách khi đọc và bị bỏ khi lưu.

## Lấy toàn bộ link video của kênh YouTube / TikTok

Tab Người dùng & Kênh: chọn kênh YouTube hoặc TikTok **đã kéo** → **🔗 Lấy toàn bộ link video** (`video_links.py`). Link lưu trong DB cục bộ (`channel_videos`: link, tiêu đề, ngày đăng,
lượt xem nếu nguồn có), xuất file `.dev/video-links/<kênh>.txt` (mới nhất trước), xem/sao chép/lưu `.txt` ở nút `🔗 N link` trên dòng kênh. Đây là danh sách link; lượt xem trong đó là
số lúc liệt kê. Việc **đo view/like/comment/share** (và đẩy lên website) do collector làm theo job — 30 video mới nhất/kênh YouTube, 15 video/kênh TikTok.

YouTube: Data API (playlist uploads + thống kê, xoay nhiều key, tới 5000 video, ~1 đơn vị quota/50 video); không có key thì yt-dlp (chỉ link). Đã đo: 2 kênh thật ra đủ 242 và 236 video trong 4 giây.

### TikTok


Cách làm (`tiktok_links.py`): yt-dlp `extract_flat` **không cookie, không trình duyệt**, lặp tối đa 25 lượt, mỗi lượt đổi proxy kế tiếp và đổi dấu vân tay TLS
(chrome-136/131/124) cho tới khi qua cổng chống bot của TikTok. Lý do — số đo thật (9 proxy dân cư VN, kênh 393 video):

| Phương án | Kết quả đo |
|---|---|
| yt-dlp + cookie lấy từ Chrome qua proxy | 1/9 proxy qua ở lượt đầu; mở trình duyệt tốn 5–17 giây/lần, KHÔNG tăng tỉ lệ qua |
| yt-dlp không cookie, một lượt | 8–11/36 lượt qua (~22–30%), ngẫu nhiên, không phụ thuộc proxy hay dấu vân tay |
| **yt-dlp không cookie, lặp tới 25 lượt (đang dùng)** | **7/7 lần ra đủ 393 link** (5–60 giây, 1–15 lượt); trượt cả 25 lượt ≈ 0,1% |
| Chrome thật cuộn trang + đọc phản hồi API | Trang đầu (24 video) qua 9/9 proxy nhưng TikTok bắt đầu trả rỗng sau ~15–19 trang: chỉ ra 112–371/393 video, mất 3,5 phút |
| tikwm `/api/user/posts` | 403 Cloudflare ở mọi proxy và IP trực tiếp: không dùng được |

Cổng chặn **ngẫu nhiên ~75% lượt gọi** (yt-dlp báo `Unable to extract secondary user ID`) nhưng lượt thất bại chỉ tốn vài giây và lượt qua cho danh sách đầy đủ,
nên lặp là cách ít lỗi nhất. Kênh trống / riêng tư được nhận ra ngay (không lặp). Hết 25 lượt thì báo rõ và gợi ý thêm/đổi proxy (nên dùng proxy dân cư).
Không có proxy thì đi bằng IP máy này (IP không tốt có thể trượt cả 25 lượt). Mỗi lần gọi bắt đầu từ một proxy ngẫu nhiên để job song song không dồn vào một proxy.


## "Chạy ngay" (không chờ lịch)

Tab Thu thập có nút **▶ Chạy ngay** ở đầu trang (mọi kênh đã kéo + đang bật), ở thanh lọc của tab Kênh & người dùng (các kênh đang hiện theo bộ lọc nền tảng) và trên từng dòng kênh.
Nó chạy `collect.py --once [--resource-ids …]`: xin job cho kênh được chọn, xử lý hết rồi **tự thoát** (khác **Bắt đầu** = chạy liên tục theo lịch). Cần máy chủ có
`POST /v1/integrations/jobs/enqueue` (commit `daff94a`); máy chủ cũ trả 405 và log báo "cần deploy integration-service bản mới".

Cột trạng thái "Chưa thu (phiên này)" của tab Kênh & người dùng đọc từ file trạng thái của collector (`.dev/collector-status.json`, số OK/lỗi của các job collector đã xử lý), còn
danh sách kênh đọc từ DB cục bộ như tab Người dùng & Kênh — hai nguồn khác nhau nên kênh đã kéo vẫn hiện "Chưa thu" cho tới khi collector xử lý job của kênh đó.
## Test

```powershell
python -m pytest tests -q   # logic đẩy số liệu, ánh xạ endpoint, file kết nối (không cần mạng/DB)
```

## Ghi chú

- `src/api.ts`, `src/auth.tsx`, `src/pages/LoginPage.tsx`, `src/pages/InventoryPage.tsx`,
  `src/components/` (FilterBar/SourceTree/QueuePanel) là phần web-shell V1 cũ (đăng nhập +
  inventory/Run Queue qua `admin-server`) — **không còn được `App.tsx` import**. Giữ lại trong
  cây nguồn để tham khảo nếu Phase D-F cần dựng lại luồng inventory; cần quyết định tường minh
  (xoá hẳn hay tách sang app khác) trước khi dọn dẹp.
