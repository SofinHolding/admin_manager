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

## Ghi chú

- `src/api.ts`, `src/auth.tsx`, `src/pages/LoginPage.tsx`, `src/pages/InventoryPage.tsx`,
  `src/components/` (FilterBar/SourceTree/QueuePanel) là phần web-shell V1 cũ (đăng nhập +
  inventory/Run Queue qua `admin-server`) — **không còn được `App.tsx` import**. Giữ lại trong
  cây nguồn để tham khảo nếu Phase D-F cần dựng lại luồng inventory; cần quyết định tường minh
  (xoá hẳn hay tách sang app khác) trước khi dọn dẹp.
