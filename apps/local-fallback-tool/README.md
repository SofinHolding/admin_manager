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

`collect.py` được đóng gói kèm theo (`build.files` trong `package.json`); máy chạy ứng dụng vẫn
cần Python (và các dependency của `collect.py`, vd. `httpx`) cài sẵn trong PATH.

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

## Kéo dữ liệu từ website: chỉ user dùng tool dùng chung

Tab **Người dùng & Kênh** (Fetch → Kéo kênh) chỉ lấy user/kênh có `execution_mode = fallback_tool` (khai báo trên web, chế độ "Máy tập trung").
User có Tool riêng đẩy kênh bằng `primary_tool` nên KHÔNG kéo về; Fetch báo rõ số user/kênh bị bỏ qua. User còn vừa có Tool riêng vừa khai
báo kênh trên web thì chỉ các kênh `fallback_tool` của họ được kéo. Mỗi lần Fetch/Kéo kênh thay thế dữ liệu cục bộ theo danh sách mới
(`prune`/`replace` trong `channels_db.py`): user hoặc kênh đã chuyển sang Tool riêng/bị xoá trên web sẽ được dọn khỏi DB. Lọc nằm ở
`system_api.shared_view` (một lần gọi `GET /resources`, không gọi theo từng owner).

## Kết nối bằng file Admin cấp

Khi Admin cấp key cho công cụ dùng chung (trang Admin → Công cụ dùng chung), giao diện cho tải
`connection-<client_id>.json` (`format: "manager-connection/1"`, chứa `gateway` + `api_key`; **là secret**, chỉ có lúc cấp/xoay).

- CLI: `python collect.py --connection-file connection-<client_id>.json --checkview-dir <thư mục check_*.py>`
- Electron: tab Thu thập → Cấu hình → **Nạp file kết nối…** (tự điền Gateway URL + API Key).

Collector hỏi server `GET /v1/integrations/platforms` để biết nền tảng key được dùng, chỉ xử lý giao với nền tảng có adapter
(`youtube`, `tiktok`, `facebook`). Mỗi job đẩy lên **đúng kênh + nền tảng của chính job** (`resource.external_id`, `job.platform`)
bằng snapshot `delta` có `expected_counts`; mọi bước được kiểm lỗi (không thành công giả). Key cần scope `jobs:claim:system` để nhận job của mọi user.
Adapter TikTok hiện là placeholder (chưa liệt kê được video) nên job TikTok sẽ `fail` kèm lý do cho tới khi hoàn thiện.

Định dạng payload collector phải gửi (snapshot delta, job complete/fail/heartbeat, ràng buộc `X-Delegated-Job`): `docs/api/integration-tool-changes-2026-10-16.md` §4.

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
