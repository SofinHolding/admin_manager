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
python -m pytest tests -q   # logic đẩy số liệu, file kết nối (không cần mạng/DB)
```

## Ghi chú

- `src/api.ts`, `src/auth.tsx`, `src/pages/LoginPage.tsx`, `src/pages/InventoryPage.tsx`,
  `src/components/` (FilterBar/SourceTree/QueuePanel) là phần web-shell V1 cũ (đăng nhập +
  inventory/Run Queue qua `admin-server`) — **không còn được `App.tsx` import**. Giữ lại trong
  cây nguồn để tham khảo nếu Phase D-F cần dựng lại luồng inventory; cần quyết định tường minh
  (xoá hẳn hay tách sang app khác) trước khi dọn dẹp.
