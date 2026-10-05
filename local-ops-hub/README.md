# Local Fallback Tool (Tool B) — v1 web shell

Triển khai V1 của `tasks/backlog/local-operations-hub-v1.md` (mục Phase C). Đăng nhập bằng
account `admin`/`dash_admin` hiện có của Admin Manager, kéo inventory từ `admin-server` (`/v1/ops/*`),
cho phép filter/chọn resource và tạo Run Queue batch.

## Vì sao web shell, không phải Tauri desktop app

Đặc tả (mục 3) khuyến nghị Tauri/Electron cho Tool B — lý do là **các nhu cầu của Phase D trở đi**
(điều khiển Chromium/AdsPower, browser profile/cookie local, filesystem, proxy, credential local).
V1 (mục 15) **chỉ cần inventory + queue**, không cào dữ liệu thật, nên không cần năng lực desktop-native
nào — một ứng dụng web thuần (Vite + React) đáp ứng đủ toàn bộ tiêu chí nghiệm thu V1 (mục 27).

Môi trường build hiện tại **không có Rust toolchain** (`cargo`/`rustc` không cài, không có MSVC Build
Tools cho linker) — không thể build/chạy thật một ứng dụng Tauri để verify. Quyết định: xây V1 dưới
dạng web app trước, đã smoke-test đầy đủ qua trình duyệt thật (login/inventory/filter/multi-select/
Run Queue/cancel — xem QA report).

### Đường dẫn sang Tauri (khi Phase D cần browser/profile/proxy)

Code trong `src/` (React, không phụ thuộc gì ngoài `fetch`/`localStorage`) port thẳng vào Tauri
webview gần như không đổi:

1. Cài Rust toolchain (`rustup` + MSVC Build Tools trên Windows) trên máy build.
2. `npm create tauri-app` hoặc `cargo tauri init` trong thư mục này, trỏ `frontendDist` vào `dist/`
   (build output Vite hiện có).
3. Các hook desktop-native (spawn Chrome qua CDP, filesystem, local SQLite cache) viết ở tầng Rust
   (`src-tauri/`) + gọi qua `@tauri-apps/api` từ React — KHÔNG cần viết lại UI/API client đã có.

## Chạy dev

```powershell
npm install
npm run dev   # http://localhost:5176/ — proxy /v1 -> http://localhost:8423 (admin-server)
```

admin-server phải đang chạy ở cổng 8423 (hoặc sửa `vite.config.ts` cho khớp cổng thật).

## Phạm vi V1 đã triển khai

- Đăng nhập account `admin`/`dash_admin` hiện có, gửi `X-Client-Type: local_fallback_tool` —
  `viewer`/`discord` bị chặn ngay ở backend (403), có audit log `login_fallback_tool`.
- Inventory: Source Tree (User → Platform → Source → Resource), filter đầy đủ 8 trục (mục 12).
- Multi-select + "Chọn tất cả (đủ điều kiện queue)" — chỉ resource `execution_mode=fallback_local`
  mới chọn được.
- Tạo Run Queue: tự tách batch theo platform khi chọn nhiều nền tảng cùng lúc (mục 14).
- Queue History: xem batch đã tạo, mở rộng xem item + trạng thái, huỷ item.
- V1 **chưa có adapter** — mọi item vừa thêm dừng ở `blocked_no_adapter`, KHÔNG giả lập `success`
  (mục 13/15, tiêu chí nghiệm thu #10).

## Chưa làm (ngoài phạm vi V1, xem đặc tả Phase D-F)

- Local SQLite cache (mục 18) — V1 dùng REST/manual refresh trực tiếp, đúng như đặc tả cho phép
  ("V1 có thể dùng REST/manual refresh nhưng phải sẵn sàng cho incremental sync, background refresh
  và SSE/WebSocket"). `opsApi.inventoryRevision()` đã có sẵn ở tầng API cho bước incremental sau này.
- Platform adapter / crawler thật.
- Đóng gói desktop (Tauri) — xem mục trên.
