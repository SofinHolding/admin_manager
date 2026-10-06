# UI Design v1 — giao diện manager-web cho mọi vai trò

Áp dụng cho `apps/manager-web`. Mục tiêu: **ai cũng dùng được** — người chủ kênh không rành kỹ thuật, quản trị viên, người vận hành Discord, người dùng trên điện thoại, người cần chữ lớn hoặc nền tối.
Tài liệu này là hợp đồng thiết kế cho mọi trang; mọi trang mới phải dùng các thành phần ở §5 và tuân quy tắc ở §6–§7.

## 1. Người dùng và việc chính của họ

| Vai trò (role) | Gọi trong giao diện | Việc chính | Đang gặp khó gì (rà soát giao diện cũ) |
|---|---|---|---|
| `viewer` | Người xem | Xem kênh của mình được xem bao nhiêu, kênh nào tốt nhất, quốc gia nào; nhóm kênh; nhập kênh thủ công | Màn đầu toàn bộ lọc kỹ thuật và ô "chưa so được"; thuật ngữ Source/Resource/Tool B; chữ 14px |
| `admin` | Quản trị viên | Tạo mã mời, cấp quyền, khóa tài khoản, cấp key cho Tool, xem mọi dữ liệu | Bảy mục menu ngang cùng cấp, không biết bắt đầu từ đâu; nhiều thao tác dồn trong một trang tab |
| `dash_admin` | Quản lý Dashboard | Như admin trừ việc nhạy cảm (không động vào quản trị viên) | Không thấy rõ mình **không** làm được gì |
| `discord` / quyền `reward:access` | Vận hành Discord | Kết nối Discord, tạo và theo dõi công việc phân phối điểm | Thuật ngữ job/credential; trạng thái chạy khó đọc |

Ba nguyên tắc chung: **(1) nói ngôn ngữ của người dùng** (kênh, lượt xem, mã mời — không nói resource, client_id, snapshot); **(2) luôn cho biết bước kế tiếp** (trạng thái rỗng và lỗi đều chỉ đường);
**(3) không bắt người dùng hiểu hệ thống** — chi tiết kỹ thuật nằm trong phần "Chi tiết kỹ thuật" có thể mở ra.

## 2. Kế hoạch thiết kế (token)

**Màu — "phòng điều khiển ban ngày"**: nền xanh sương lạnh, mực xanh lục đậm, một màu nhấn ấm duy nhất cho điểm cần nhìn. Không kem/terracotta, không nền đen + xanh acid.

| Tên | Sáng | Tối | Dùng cho |
|---|---|---|---|
| Sương (`background`) | `oklch(0.975 0.006 205)` | `oklch(0.19 0.02 215)` | Nền trang |
| Giấy (`surface-1`) | `oklch(1 0 0)` | `oklch(0.235 0.022 215)` | Vùng nội dung, bảng |
| Mực (`foreground`) | `oklch(0.24 0.035 215)` | `oklch(0.955 0.008 205)` | Chữ chính |
| Xanh lục đậm (`primary`) | `oklch(0.47 0.085 200)` | `oklch(0.74 0.10 195)` | Hành động chính, mục đang chọn |
| Hổ phách (`accent-2`) | `oklch(0.80 0.15 85)` | `oklch(0.82 0.14 85)` | MỘT điểm nhấn: con số/đường được nhấn mạnh, mục "mới" |
| Trạng thái | thành công `0.50/0.12/150`, cảnh báo `0.52/0.12/70`, lỗi `0.50/0.19/25` | sáng hơn tương ứng | Luôn kèm biểu tượng và chữ, không chỉ màu |

Biểu đồ dùng 6 màu phân biệt được cả với người mù màu (xanh lục, chàm, hổ phách, hồng đất, lục, xám xanh) + nhãn trực tiếp.

**Chữ — Be Vietnam Pro** (thiết kế riêng cho tiếng Việt, tự host qua `@fontsource`, không gọi dịch vụ ngoài), một họ duy nhất; mono hệ thống chỉ cho mã/khóa.
Thang: nền **16 px** (người dùng chọn 100 % / 112 % / 125 % trong menu), `h1` 1.75 rem/700, `h2` 1.25 rem/650, nhãn 0.875 rem/600, chữ phụ 0.875 rem. Số liệu dùng `tabular-nums`. Độ dài dòng ≤ 72 ký tự cho đoạn văn.
Không viết hoa toàn bộ nhãn, không nhãn nhỏ phía trên mọi tiêu đề.

**Bố cục — thanh bên theo công việc, nội dung theo mạch đọc**
```text
Desktop ≥1024                              Mobile <1024
┌──────────┬──────────────────────────┐    ┌───────────────────────────┐
│ ▣ Manager│ Tổng quan                │    │ ☰  Tổng quan        ◐  👤 │
│ THEO DÕI │ ─────────────────────────│    ├───────────────────────────┤
│  Tổng quan│ <câu tóm tắt có số>     │    │ <câu tóm tắt có số>       │
│  Nhóm kênh│ [7 ngày][30][90][Tùy chọn]│   │ [7][30][90][Tùy chọn]     │
│ KẾT NỐI  │ ── biểu đồ chính ───────  │    │ ── biểu đồ chính ──       │
│  Kênh…   │ ── kênh nổi bật | quốc gia│    │ ── kênh nổi bật ──        │
│ QUẢN TRỊ │                          │    ├───────────────────────────┤
│ ─────────│                          │    │ Tổng quan Nhóm Kết nối Thêm│
│ 👤 tên ▾ │                          │    └───────────────────────────┘
└──────────┴──────────────────────────┘
```
- Thanh bên gom mục theo **việc** (Theo dõi / Kết nối dữ liệu / Phân phối điểm / Quản trị), chỉ hiện nhóm mà vai trò có quyền; khối người dùng ở đáy có hồ sơ, giao diện sáng/tối/theo máy, cỡ chữ, đăng xuất.
- Mobile: thanh trên + ngăn kéo điều hướng đầy đủ + thanh tab dưới cho 4 đích chính + "Thêm".
- Căn trái toàn bộ; nội dung rộng tối đa 72 rem; mỗi trang có **một** cột đọc chính, không lưới thẻ giống nhau.

**Nguyên tắc nhận diện (một điểm nhấn):** *câu tóm tắt bằng lời có số* ở đầu trang Tổng quan ("Kênh của bạn được xem 797.604 lần trong 30 ngày qua — nhiều hơn 12% so với 30 ngày trước") — đây là thứ đầu tiên người dùng đọc; mọi thứ khác yên tĩnh.
Các vùng nội dung ngăn bằng đường kẻ mảnh, **không** bọc mọi thứ trong thẻ bo góc + bóng; chỉ lớp phủ (hộp thoại, menu) mới có bóng. Bán kính: điều khiển 8 px, hộp thoại 14 px, vùng nội dung 0.

## 3. Rà soát kế hoạch so với brief (đã chỉnh sau khi rà)

| Phát hiện khi tự rà | Điều chỉnh |
|---|---|
| Thanh bên + KPI + thẻ + xanh lục có thể trông như "SaaS dashboard mẫu" | Bỏ hàng 3 thẻ KPI → một **câu tóm tắt** + dải số liệu liền mạch ngăn bằng đường kẻ; bỏ khung thẻ quanh mọi thứ |
| Hổ phách + xanh lục dễ thành "gradient trang trí" | Cấm gradient; hổ phách chỉ dùng cho **một** điểm nhấn mỗi màn hình |
| Nhãn nhỏ viết hoa trên nhóm điều hướng là dấu hiệu rập khuôn | Tiêu đề nhóm viết thường câu ("Theo dõi"), chữ 0.8125 rem/600, màu phụ |
| Brief nói "đa dạng người dùng" — chưa đủ nếu chỉ đổi màu | Thêm: cỡ chữ 3 mức, sáng/tối/theo máy, ngôn ngữ giản dị, điều hướng điện thoại riêng, trạng thái rỗng có hướng dẫn, hạn chế phụ thuộc màu |
| Hoạt ảnh | Không tự chuyển động; chỉ phản hồi hành động (mở ngăn kéo, xác nhận); tôn trọng `prefers-reduced-motion` |

## 4. Chất lượng nền (không thương lượng)

Tương phản chữ ≥ 4.5:1 (chữ lớn ≥ 3:1) ở cả sáng và tối; vòng focus 2 px rõ ở mọi điều khiển; mọi thao tác dùng được bằng bàn phím; mục chạm ≥ 40 px (44 px trên điện thoại);
`lang="vi"`, liên kết "Bỏ qua điều hướng", landmark `nav/main`; không thông tin chỉ bằng màu; không cuộn ngang trang ở 360 px; `prefers-reduced-motion` được tôn trọng; số/ngày theo định dạng Việt Nam.

## 5. Thành phần dùng chung (`src/components/`)

| Thành phần | Dùng để |
|---|---|
| `AppShell` (Layout) | Khung thanh bên / thanh tab, người dùng, giao diện, cỡ chữ |
| `PageHeader` | Tiêu đề trang + một câu mô tả việc trang làm + vùng hành động chính (một nút chính mỗi trang) |
| `Section` | Một vùng nội dung có tiêu đề, mô tả ngắn, hành động; ngăn bằng đường kẻ |
| `StatStrip` | Dải số liệu liền mạch (nhãn, giá trị, so sánh bằng lời) thay cho hàng thẻ KPI |
| `EmptyState` | Trạng thái rỗng: nói đang thiếu gì + nút/liên kết bước kế tiếp |
| `Notice` | Thông báo trong trang (thông tin/cảnh báo/lỗi/thành công) có biểu tượng + chữ; lỗi nói **chuyện gì xảy ra và cách sửa** |
| `Field` | Nhãn + ô nhập + gợi ý + lỗi, liên kết `aria-describedby` |
| `TechDetails` | Khối "Chi tiết kỹ thuật" gập được cho id, khóa, JSON |
| `ui/*` (Button, Input, Select, Tabs, Badge, Dialog…) | Đã đổi kiểu, giữ nguyên API |

## 6. Quy tắc viết (copy)

Tiếng Việt giản dị, câu chủ động, viết hoa kiểu câu. Tên thao tác giữ nhất quán từ nút đến thông báo ("Tạo mã mời" → "Đã tạo mã mời").
Gọi theo việc người dùng hiểu: **kênh** (không "resource"), **nguồn dữ liệu / công cụ đẩy dữ liệu** (không "Tool B/client"), **mã mời** (không "invite key"), **khóa kết nối** (API key, kèm "chỉ hiện một lần"), **công việc phân phối điểm** (không "job").
Lỗi: nói điều gì xảy ra + cách sửa, không xin lỗi chung chung, không mã lỗi trơ trụi (mã nằm trong "Chi tiết kỹ thuật"). Trống: mời hành động.

## 7. Quy tắc theo vai trò

- `viewer` không thấy mục Quản trị; trang Quản trị cho `dash_admin` ghi rõ việc không được làm (khóa/sửa/xóa quản trị viên).
- Mục điều hướng ẩn/hiện theo quyền (server vẫn là nơi cưỡng chế).
- Trang đầu (`homePath`) giữ nguyên logic: người chỉ có quyền Reward vào "Công việc phân phối điểm".

## 8. Việc nằm ngoài phạm vi v1

Đa ngôn ngữ (chỉ tiếng Việt), chế độ độ tương phản cao riêng (đã đạt AA), thiết kế lại biểu đồ bằng thư viện khác (giữ `recharts`), trình soạn nội dung, bảng điều khiển tùy biến theo người dùng.
