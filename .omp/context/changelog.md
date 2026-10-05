# changelog.md

## 2026-09-28 — Khởi tạo context (vibe-init)

- Quét toàn bộ 3 service (`admin-server`, `admin-web`, `reward-service`) bằng scout agent song song +
  đọc trực tiếp README.md/DEPLOY.md.
- **Đối chiếu chéo với repo nguồn dữ liệu `D:/workspace/upload-facebook`** (repo Tool, có sẵn
  `.omp/context/*` từ trước) để lấy: schema thật bảng `documents`/`revs`/`user_meta` (từ
  `server/db.py`), whitelist store đồng bộ (`app/sync/registry.py`), xác nhận `stats_core.py` 2 bên
  byte-identical (diff -u), làm rõ 2 khái niệm "admin" khác nhau (`admin_manager` vs
  `upload-facebook/app/admin/`).
- Sinh: `project.md`, `modules.md`, `data-models.md`, `conventions.md`, flows (`login-auth`,
  `dashboard-stats`, `reward-job-lifecycle`, `discord-command-flow`), `AGENTS.md`, `RULES.md`.
- Phát hiện cần theo dõi (ghi trong RULES.md § Ràng buộc kỹ thuật): lệch cổng dev admin-server giữa
  comment `reward/api.ts` (8421) và `admin-web/vite.config.ts` thực tế (8423); `admin_api.require_admin`
  không tra DB (JWT còn hạn dùng được dù account đã suspended); CORS production domain thật không thấy
  trong `main.py`.

## 2026-09-30 — Dashboard đa nền tảng + phân quyền dữ liệu theo Tool + group kênh

- **Phân quyền dữ liệu theo `user_id` (Tool)**: trước đây `admin-server` đọc `documents` XUYÊN USER
  tuyệt đối, không lọc gì (`db.py` comment cũ "ĐỌC XUYÊN USER"). Nay mọi hàm `_load_*`/`_load_*_pg`
  và 5 method viewer (`traffic_metric`,`summary`,`top_channels`,`views_by_country`,`country_tags`)
  nhận thêm `user_ids: list[str] | None` — `None` = không lọc (mặc định cho `admin`/`dash_admin`),
  `[]`/danh sách cụ thể = lọc theo `documents.user_id`. `LocalFileDb` (dev, đọc JSON 1 thư mục) NHẬN
  nhưng BỎ QUA tham số này — dev mode vẫn single-tenant theo bản chất (1 thư mục = 1 Tool).
- **Bảng mới `account_links`/`link_codes`**: liên kết account dashboard ⇄ `user_id` Tool qua mã
  pairing dùng 1 lần (TTL 15 phút), sinh tại `POST /v1/viewer/links/code`, Tool đổi mã lấy liên kết
  tại `POST /v1/viewer/links/redeem` (public, rate-limit — endpoint đã có, giao diện nhập mã phía
  Tool CHƯA triển khai, nằm ở repo `upload-facebook`, ngoài phạm vi lượt này). KHÔNG đụng cơ chế
  session/license-key hiện có của Tool (`server/main.py::_user_id_of` giữ nguyên).
- **Bảng mới `channel_groups`/`channel_group_members`**: group/tag kênh do viewer tự quản lý
  (`groups.py`), sở hữu riêng bởi admin-server — KHÔNG liên quan `repost_groups`
  (dữ liệu nghiệp vụ reup thật của Tool, ảnh hưởng `stats_core.cho_chay`). `platform_tag` là nhãn tự
  do trên từng kênh trong group — đáp ứng yêu cầu "đa nền tảng" mà KHÔNG đụng `stats_core.py` (vẫn
  byte-identical với Tool, đã diff xác nhận).
- **Role mới `dash_admin`** ("Admin Dashboard/ADD") — cộng thêm, KHÔNG thay `admin` cũ.
  `admin_api.require_admin` nay chấp nhận cả `admin` và `dash_admin`. Route mới `owner_account_id`
  trên các endpoint viewer cho phép `admin`/`dash_admin` soi dữ liệu 1 account cụ thể.
- **`group_id` filter** áp dụng cho cả 5 endpoint viewer bằng cách thu hẹp `fb_posts`/`yt_daily`/
  `channel_meta`/`target_country` NGAY TẠI `db.py` trước khi gọi `stats_core` (helper
  `_apply_channel_refs`) — vì chỉ 2/5 hàm `stats_core` có tham số `yt_filter`/`fb_filter`, còn
  `top_channels`/`views_by_country`/`country_tags` thì không; lọc trước input là cách DUY NHẤT áp
  dụng thống nhất mà không sửa `stats_core.py`.
- Cache `SCHEMA` bump `v1→v2` (shape `summary` thêm field `linked`, mọi method cache nhận thêm
  `user_ids`/`channel_refs` vào cache key).
- File mới: `admin-server/links.py`, `admin-server/groups.py`. Sửa: `db.py`, `cache.py`, `viewer.py`,
  `admin_api.py`, `main.py`. Frontend: `admin-web/src/routes/GroupsPage.tsx` (mới) + cập nhật
  `ProfilePage.tsx`, `DashboardPage.tsx`, `AdminPage.tsx`, `App.tsx`, `RoleGate.tsx`, `api.ts`.
- Smoke test thủ công (throwaway script, đã xoá sau khi verify): cô lập dữ liệu 2 Tool user_id khác
  nhau qua 2 account viewer, dash_admin xem xuyên-user + soi theo `owner_account_id`, mã link dùng 1
  lần, group CRUD chặn sửa hộ — TẤT CẢ PASS.

## 2026-09-30 (tiếp) — Code review + vá 2 lỗ hổng phát hiện được

- **[ĐÃ VÁ, mức CAO]** `POST /v1/viewer/links/redeem` trước đây chỉ xác thực mã (chứng minh ai sở
  hữu account) mà KHÔNG xác thực `tool_user_id` có thật thuộc về ai — kẻ tấn công tự tạo mã bằng
  account của chính họ rồi redeem với `tool_user_id` của NẠN NHÂN (nếu biết được) sẽ âm thầm gắn
  dữ liệu Tool của nạn nhân vào account của chính mình, nạn nhân không được báo. Đã vá: thêm
  `Db.tool_user_id_exists()` (query `documents`, fail-closed nếu bảng chưa có), kiểm TRƯỚC khi đốt
  mã — `tool_user_id` sai thì mã VẪN CÒN HIỆU LỰC để thử lại (chỉ chặn ID bịa/đoán mò, KHÔNG chặn
  được kẻ đã biết đúng `tool_user_id` thật — rủi ro này CHƯA đóng hoàn toàn, cần thiết kế xác thực
  chữ ký xuyên-repo với Tool (`SYNC_PEPPER`) trước khi Tool-side UI thật đi vào production).
- **[ĐÃ VÁ, mức TRUNG BÌNH]** `dash_admin` trước đây ngang quyền tuyệt đối với `admin` qua
  `admin_api.require_admin`, có thể tự leo thang: tạo invite-key `role=admin` hoặc PATCH role người
  khác (kể cả chính mình) thành `admin`. Đã vá: `create_key`/`update_user` chặn gán `role=admin`
  trừ khi caller đã LÀ `admin` thật — `dash_admin` vẫn tạo/gán được `viewer`/`discord`/`dash_admin`
  bình thường (đúng phạm vi "quản lý người dùng dashboard").
- File sửa: `admin-server/{db.py,admin_api.py}`. Smoke test throwaway (đã xoá sau khi verify): mã
  không bị đốt khi `tool_user_id` sai + redeem lại thành công với ID thật; `dash_admin` bị chặn 403
  ở cả 2 đường leo thang — TẤT CẢ PASS.

## 2026-09-30 (tiếp) — Biểu đồ riêng/chung theo đúng kế hoạch gốc + lọc nền tảng

- **Bộ lọc "Nền tảng" (mới)**: `Db.channel_refs_by_platform()`/`list_platform_tags()` (mới) — tra
  `channel_group_members.platform_tag` (nhãn tự do đã gắn ở Nhóm kênh), scope theo `account_ids`
  giống hệt cơ chế `group_id`. `viewer.py::_resolve_scope` nay nhận thêm `platform_tag`, GIAO
  (intersect) với `group_id` nếu cả 2 cùng truyền. Endpoint mới `GET /v1/viewer/platform-tags`.
- **Tab "Biểu đồ riêng" / "Biểu đồ chung"** (`DashboardPage.tsx`) — trước đây chỉ có bộ lọc, chưa
  thực sự tách 2 khái niệm theo đúng plan gốc:
  - Biểu đồ riêng = nội dung cũ (Summary/Traffic/TopChannels/CountryViews, lọc theo quốc gia/nhân
    sự/nhóm/nền tảng) + THÊM `PlatformBreakdownCard`: khi chọn 1 quốc gia cụ thể, hiện pie phân rã
    quốc gia đó theo nhãn nền tảng (đúng ví dụ trong plan: "Nigeria 1 triệu view → 400k tiktok,
    600k youtube").
  - Biểu đồ chung = `ComparisonView` (mới) — 5 card so sánh: quốc gia (tận dụng lại `totals` có sẵn
    trong response `views_by_country`, KHÔNG gọi API mới), nền tảng/nhân sự/nhóm kênh (fan-out
    `Promise.all` gọi lại `summary()` có sẵn với từng lát cắt), kênh (tận dụng lại `top_channels`
    đã tải cho tab Riêng). Không thêm endpoint tổng hợp mới ở backend — toàn bộ dựng từ API đã có.
- **Sắp xếp lại filter bar + ẩn theo ngữ cảnh**: "Chọn nhân sự" chuyển lên TRƯỚC "Nhóm kênh"/"Nền
  tảng". Với `admin`/`dash_admin`: dropdown Nhóm kênh + Nền tảng CHỈ hiện sau khi chọn 1 nhân sự cụ
  thể (chưa chọn ai — "Tất cả nhân sự" — thì ẩn, vì gộp nhóm/nhãn của nhiều người không có chủ sở
  hữu rõ ràng). Với `viewer` thường: luôn hiện (vì luôn có đúng 1 "chủ sở hữu" = chính họ), nhưng ẩn
  nếu người đó chưa có nhóm/nhãn nào (`groupsList.length === 0` / `platformTags.length === 0`).
- File sửa: `admin-server/{db.py,viewer.py}`, `admin-web/src/{api.ts,routes/DashboardPage.tsx}`.
- Verify bằng browser thật với dữ liệu seed đa nhân sự (`seed_qa_demo.py`): "So sánh nhân sự" ra
  đúng nv_lan 1.167.339/nv_tuan 563.470/nv_minh 422.817 (khớp số đã verify riêng lẻ); "So sánh nền
  tảng" facebook 634.499/instagram 8.572/tiktok 7.223 (khớp số verify qua API trực tiếp); pie phân
  bổ nền tảng theo quốc gia Nigeria render đúng; filter bar ẩn/hiện đúng theo vai trò + trạng thái
  chọn nhân sự.

## 2026-09-30 (tiếp) — Biểu đồ chung: linh hoạt Cột/Đường/Tròn như Biểu đồ riêng

- Mỗi card "So sánh ..." (`ComparisonChart`, thay `CompareBarList` cũ) nay có toggle **Cột/Đường/
  Tròn** giống hệt `TrafficMetricChart` ở tab Biểu đồ riêng — không còn cố định 1 dạng bar-list.
  "So sánh kênh" chỉ có Cột/Tròn (không có Đường — không có API trả view/kênh/ngày, không bịa số).
- Đổi nguồn fetch fan-out từ `summary()` sang `traffic()` cho nền tảng/nhân sự/nhóm — 1 lượt gọi
  trả CẢ tổng (`totals.views`, dùng cho Cột/Tròn) LẪN chuỗi theo ngày (`days[].views`, dùng cho
  Đường), không cần gọi 2 API riêng. "So sánh quốc gia" tái dùng nguyên `views_by_country` đã tải
  cho tab Riêng (đã có sẵn cả `totals` lẫn `days` theo quốc gia).
  Áp dụng rule dự án `ts-set-map`: gộp dữ liệu theo ngày dùng `Record<string,...>` (khoá string
  tĩnh trong vòng lặp), KHÔNG dùng `Map`.
- **Tính tương tác chéo bộ lọc — ĐÃ có sẵn từ trước, không phải code mới**: filter bar (quốc gia/
  nhân sự/nhóm/nền tảng) nằm NGOÀI 2 tab nên áp dụng chung cho cả "Biểu đồ chung" qua `apiOpts`/
  `country` — chọn 1 quốc gia cụ thể → so sánh nền tảng/nhân sự/nhóm tự thu hẹp về đúng quốc gia đó
  (verify: filter Nigeria → so sánh nền tảng đổi facebook 634.499→601.415, tiktok 7.223→1.425). "So
  sánh quốc gia" CỐ Ý không nhận filter quốc gia (nếu nhận sẽ chỉ còn 1 lát, vô nghĩa cho biểu đồ
  so sánh) — luôn hiện đủ mọi quốc gia trong phạm vi đang xem.
- File sửa: `admin-web/src/routes/DashboardPage.tsx`.
- Verify bằng browser thật: cả 3 dạng Cột/Đường/Tròn render đúng trên "So sánh quốc gia" (Đường ra
  3 đường xu hướng theo ngày đúng màu/tên; Tròn ra donut đúng tỉ lệ); lọc theo quốc gia làm các
  card so sánh khác đổi số đúng như mô tả.

## 2026-09-30 (tiếp) — Phóng to biểu đồ chung (modal)

- Thêm `admin-web/src/components/ui/dialog.tsx` (mới, dựa `@radix-ui/react-dialog` — cài thêm
  dependency, theo đúng pattern Radix sẵn có của `select.tsx`/`tabs.tsx`, bỏ class
  `tailwindcss-animate` vì plugin đó chưa cấu hình trong dự án).
- Mỗi card `ComparisonChart` (Biểu đồ chung) thêm nút "Phóng to" (icon `Maximize2`) cạnh toggle
  Cột/Đường/Tròn → mở modal lớn (`max-w-5xl`, cao `65vh`) render Y HỆT nội dung/số liệu đang xem
  (tách logic vẽ chart ra `renderBody(heightCls)` dùng chung cho cả bản nhỏ lẫn bản phóng to, tránh
  lệch số liệu giữa 2 nơi). Đổi dạng Cột/Đường/Tròn trong modal vẫn hoạt động bình thường.
- File sửa: `admin-web/src/routes/DashboardPage.tsx`, `admin-web/package.json` (thêm
  `@radix-ui/react-dialog`).
- Verify bằng browser thật: mở modal "So sánh quốc gia", chuyển Cột→Đường bên trong modal ra đúng
  3 đường xu hướng cỡ lớn dễ đọc; phím Escape đóng modal đúng theo hành vi Radix Dialog chuẩn.

## 2026-09-30 (tiếp) — Gộp 2 nút nền tảng thành 1 + đồng bộ quốc gia⇄nền tảng khi map dữ liệu

- **Gộp nền tảng**: trước đây có 2 khái niệm "nền tảng" độc lập — dropdown "Mọi nền tảng" ở filter
  bar (lọc theo `platform_tag` tự gắn) và dropdown "Tổng/YouTube/Facebook" riêng bên trong Traffic
  chart (`scope` gốc, chỉ ảnh hưởng đúng 1 chart). Đã gộp còn ĐÚNG 1 ô chọn — nằm trong Traffic
  chart, mở rộng thêm 2 giá trị dự trữ `youtube`/`facebook` (suy trực tiếp từ cấu trúc dữ liệu Tool
  qua `Db.channel_refs_by_native_platform()` mới, KHÔNG cần gắn nhãn tay) bên cạnh nhãn tự do —
  chọn 1 trong 2 giá trị dự trữ đó áp dụng CHO CẢ TRANG (Summary/TopChannels/CountryViews/Comparison
  qua `channel_refs`, không chỉ riêng Traffic như trước). Bỏ hẳn query param `scope` ở
  `/v1/viewer/traffic` — chỉ còn `platform_tag` thống nhất.
- **Đồng bộ quốc gia⇄nền tảng khi map dữ liệu**: cột `channel_group_members.country_override` tồn
  tại trong schema từ đầu nhưng CHƯA TỪNG được đọc ở đâu — viewer gắn quốc gia mới cho 1 kênh khi
  map vào group thì KHÔNG có tác dụng gì. Đã vá: `Db.country_overrides()` (mới) + helper
  `_apply_country_overrides()` áp NGAY vào `target_country_map`/`channel_meta` ở CẢ 5 phương thức
  viewer TRƯỚC khi lọc theo `channel_refs` — country_tags/views-by-country/summary/traffic/
  top-channels đều thấy quốc gia mới NGAY LẬP TỨC, không cần đồng bộ lại gì thêm. UI: thêm ô
  "Quốc gia (ghi đè)" cạnh "Nhãn nền tảng" trong form thêm kênh ở `GroupsPage.tsx` — 1 hành động map
  gán CẢ 2 thuộc tính (nền tảng + quốc gia) cùng lúc, đúng nghĩa "đồng bộ".
- Cache `SCHEMA` bump `v2→v3` (cache key đổi shape — thêm `country_overrides`).
- File sửa: `admin-server/{db.py,viewer.py,cache.py}`, `admin-web/src/{api.ts,routes/{DashboardPage,
  GroupsPage}.tsx}`.
- Verify bằng browser thật + API trực tiếp: `platform_tag=facebook` (native) khớp 100% tổng
  view của tài khoản 100%-Facebook-thật; `platform_tag=youtube` (native) = 0 (đúng, không có dữ
  liệu YouTube thật); gắn `country_override="XYZ-Test"` cho 1 kênh → `country-tags`/`top-channels`
  đổi ngay; dropdown nền tảng hợp nhất hiện đủ YouTube/Facebook/instagram/tiktok trong 1 ô; dropdown
  quốc gia ở Dashboard hiện "SuperTestCountry" ngay sau khi gán ở trang Nhóm kênh, không cần thao
  tác đồng bộ thủ công.

## 2026-09-30 (tiếp) — Chuẩn hoá quốc gia: 1 quy tắc duy nhất cho toàn dự án

- **Vấn đề thật**: mỗi máy Tool do 1 người tự gõ tay tên quốc gia khi cấu hình page/kênh (vd "BR"/
  "Brazil"/"brazil" đều 1 nước) — Tool không validate được từ xa. Đẩy lên thì các biến thể KHÔNG tự
  gộp — vỡ mọi biểu đồ/so sánh theo quốc gia (vd "So sánh quốc gia" ra 3 dòng riêng cho cùng 1 nước).
- **Giải pháp**: bảng mới `country_aliases` (`alias TEXT PK` viết thường đã trim → `canonical TEXT`)
  do `admin`/`dash_admin` tự quản lý qua UI (`AdminPage.tsx` tab mới "Chuẩn hoá quốc gia") — KHÔNG
  cần sửa code/deploy lại khi phát hiện biến thể mới, và **KHÔNG đụng gì tới Tool**
  (`upload-facebook`) — áp dụng hoàn toàn ở tầng đọc dữ liệu bên admin-server.
- **Điểm áp dụng**: `_normalize_country()` (mới, module-level, dùng chung `SqliteDb`/`PostgresDb`)
  chạy NGAY bên trong `_fb_page_meta`/`_yt_channels_meta` (native, cả 2 backend + `LocalFileDb`) VÀ
  `country_overrides()` (viewer tự gõ khi map kênh vào group) — TRƯỚC khi dữ liệu vào `stats_core`.
  Không đụng `stats_core.py` (vẫn byte-identical), không cần sửa `_apply_country_overrides`/
  `_apply_channel_refs` hay bất kỳ signature nào của 5 phương thức viewer — chuẩn hoá "vô hình",
  gọn hơn nhiều so với 2 lần thêm tham số trước đó (`channel_refs`, `country_overrides`).
- **Seed mặc định**: 81 dòng alias khởi tạo hợp lý (mã ISO 2 chữ + biến thể thường gặp) cho ~40 thị
  trường phổ biến với nghiệp vụ reup FB/YouTube (Brazil, Nigeria, Việt Nam, Philippines, Indonesia,
  Ấn Độ, ...) — chèn tự động lúc `setup()` (`INSERT OR IGNORE`/`ON CONFLICT DO NOTHING`, an toàn khi
  chạy lại). Admin tự thêm/xoá biến thể mới qua UI, không giới hạn ở danh sách khởi tạo.
- **Cache**: mọi thao tác thêm/xoá alias tự gọi `cache.invalidate_all()` — số liệu đúng ngay, không
  bắt admin phải nhớ bấm "Làm mới" riêng.
- File sửa: `admin-server/{db.py,admin_api.py}`, `admin-web/src/{api.ts,routes/AdminPage.tsx}`.
- Verify bằng browser thật + API trực tiếp: gán `country_override="brazil"` (thường) cho 1 kênh →
  tự động hiện thành "Brazil" (không tạo quốc gia trùng lặp trong `country_tags`); thêm alias
  "PHILS"→"Philippines" qua UI → gộp đúng vào nhóm Philippines có sẵn (ph/phil/philippines); xoá
  alias qua UI hoạt động đúng.

## 2026-10-03 — Chuẩn hoá quốc gia: tên chuẩn là master list, không còn gõ tay tự do

- **Vấn đề phát sinh từ tính năng trước**: ô "Tên chuẩn muốn gộp về" là text tự do — gõ sai/lệch
  hoa-thường (vd "brazil" thay vì "Brazil" đã có) tạo ra 1 NHÓM MỚI tách riêng thay vì gộp đúng vào
  nhóm cũ, không có cách nào sửa trừ xoá-tạo-lại thủ công từng alias.
- **Bảng mới `country_canonicals`** (`name TEXT PK`, `created_at`) — danh sách tên quốc gia "chính
  thức" do người dùng tự tạo/sửa/xoá qua UI. Backfill tự động lúc `setup()` từ mọi `canonical` đã
  tồn tại trong `country_aliases` (mặc định + dữ liệu cũ) — `INSERT OR IGNORE ... SELECT DISTINCT`,
  tự hồi phục cho DB đã tồn tại trước khi bảng này ra đời, không cần migration tay.
- **`country-aliases` (POST) nay validate canonical phải khớp 1 dòng trong `country_canonicals`**
  (400 nếu chưa tồn tại) — và LUÔN dùng đúng chữ hoa/thường đã lưu trong canonical (không dùng
  nguyên văn body gửi lên), loại bỏ hoàn toàn rủi ro gõ lệch case tạo nhóm trùng.
- **4 endpoint mới** (`/v1/admin/country-canonicals*`, cùng quyền `admin`/`dash_admin` như alias):
  `GET` liệt kê, `POST` tạo mới (409 nếu trùng tên không phân biệt hoa/thường), `PATCH {name}` đổi
  tên (cascade `UPDATE country_aliases SET canonical=... WHERE canonical=...` trong cùng transaction
  ở Postgres, cùng 1 `commit()` ở SQLite — xoá cache vì số liệu theo tên cũ sẽ sai lệch), `DELETE
  {name}` chặn 409 nếu còn alias nào đang dùng tên chuẩn này (phải xoá hết biến thể trong nhóm trước).
- **UI `AdminPage.tsx` tab "Chuẩn hoá quốc gia"**: thêm khối "Tên quốc gia chuẩn" (tạo mới + list
  chip mỗi tên chuẩn có nút đổi tên inline/xoá); ô "Tên chuẩn muốn gộp về" ở form "Thêm biến thể
  mới" đổi từ `<input>` tự do sang `<Select>` (Radix, cùng pattern dropdown vai trò có sẵn) — người
  dùng CHỌN, không gõ tay.
- File sửa: `admin-server/{db.py,admin_api.py}`, `admin-web/src/{api.ts,routes/AdminPage.tsx}`.
- Verify bằng browser thật: tạo tên chuẩn mới "Nhật Bản Test" → hiện ngay trong dropdown; gán alias
  "jp-test" qua dropdown → nhóm đúng; đổi tên "Nhật Bản Test"→"Nhật Bản" → nhóm alias bên dưới cascade
  đúng theo tên mới; xoá tên chuẩn khi còn 1 alias đang dùng → bị chặn 409 đúng thông báo; xoá alias
  rồi xoá tên chuẩn → thành công, dọn sạch dữ liệu test.

## 2026-10-03 (tiếp) — Vá lỗi: xoá alias/tên chuẩn quốc gia bị "hồi sinh" sau mỗi lần restart

- **Lỗi phát hiện qua user report** (kèm ảnh chụp màn hình): xoá 28/33 tên chuẩn quốc gia (chỉ giữ
  Brazil/Hàn Quốc/Indonesia/Nigeria/Việt Nam) thành công, nhưng SAU KHI RESTART `admin-server`, toàn
  bộ 33 tên chuẩn quay lại y như cũ.
- **Nguyên nhân**: `setup()` (cả `SqliteDb` lẫn `PostgresDb`) seed 81 alias mặc định
  (`_DEFAULT_COUNTRY_ALIASES`) bằng `INSERT OR IGNORE`/`ON CONFLICT DO NOTHING` **vô điều kiện mỗi
  lần khởi động** — pattern này chỉ chặn được TRÙNG LẶP khi dòng còn tồn tại, KHÔNG chặn được việc
  chèn lại dòng ĐÃ BỊ XOÁ HẲN (không còn gì để "conflict" nữa). Kết quả: mọi lần restart = âm thầm
  undo mọi thao tác xoá alias/tên chuẩn quốc gia mà admin đã làm qua UI.
- **Đã vá**: thêm điều kiện `existing == 0` (đếm `COUNT(*) FROM country_aliases`) TRƯỚC khi seed —
  chỉ chèn 81 alias mặc định đúng 1 LẦN DUY NHẤT lúc bảng thực sự rỗng (DB hoàn toàn mới), các lần
  restart sau KHÔNG đụng gì tới bảng này nữa dù rỗng hay không (tôn trọng mọi thao tác xoá của
  admin). Backfill `country_canonicals` từ `country_aliases` hiện có vẫn giữ nguyên logic cũ (không
  có rủi ro hồi sinh vì chỉ đọc từ bảng alias hiện tại, không có danh sách mặc định riêng).
- File sửa: `admin-server/db.py` (`SqliteDb.setup`, `PostgresDb.setup`).
- Verify: dọn DB về đúng 5 tên chuẩn, **restart `admin-server` 2 lần liên tiếp**, query DB trực tiếp
  sau mỗi lần — cả 2 lần đều giữ nguyên đúng 5 tên chuẩn + 17 alias, không hồi sinh. Verify thêm bằng
  browser thật trên tab "Chuẩn hoá quốc gia" — khớp 100% với DB.

## 2026-10-03 — Reward: sửa `unknown` hàng loạt + xoay vòng nhiều token Discord

- **Nguyên nhân gốc (đã đối chiếu DB + Discord trên VPS)**: (1) job chụp `success_pattern` rỗng nên sửa
  credential sau đó không tới job cũ; (2) `confirmation.py` chốt tin bot RỖNG (interaction defer) rồi đẩy
  con trỏ `after` qua nó → không bao giờ thấy nội dung bot điền sau → `BOT_REPLY_UNMATCHED` dù XP đã
  được cộng (17/17 item job 4 thực tế đã thành công); (3) sửa nhầm Guild ID ⇒ credential `invalid` ⇒ nút Chạy bị chặn.
- **Sửa**: `engine/confirmation.py` (quét lại tin rỗng, `authorization` theo từng lần gọi), `domain/defaults.py`
  (`DEFAULT_SUCCESS_PATTERN`), `api/jobs.py` (job mới không chụp pattern rỗng), `engine/worker_manager.py`
  (bổ sung pattern cho job cũ; mọi kiểm tra chạy TRƯỚC khi chiếm lock/đặt `running`).
- **Xoay vòng token**: migration `002_reward_tokens.sql` (bảng `reward_discord_tokens`, `reward_attempts.token_id`,
  chuyển token cũ, gỡ cột token khỏi `reward_discord_credentials`); `engine/token_pool.py` (chọn token NGẪU NHIÊN,
  không lặp token vừa dùng; nhịp chung ngẫu nhiên [0.5,1.5]×(delay+jitter/2)/N; nhịp riêng mỗi token ≥ delay;
  failover khi 401/403/404, loại token theo chuỗi unknown); API `POST/PATCH/DELETE
  /v1/reward/credentials/tokens[/{id}]` + `/{id}/verify`; `PUT /credentials` không còn nhận `token`.
  UI: `CredentialsPage.tsx` quản lý danh sách token. Job vẫn xử lý TUẦN TỰ (xác nhận reply theo bot_id/anchor
  sẽ nhầm item nếu chạy song song).

## 2026-10-03 (tiếp) — Reward: chống kẹt `unknown` (đối soát lịch sử kênh) + chọn token ngẫu nhiên

- **`engine/reconcile.py` (mới)**: `unknown` ≠ chưa cộng điểm. Quét kênh từ lúc gửi (neo `reward_attempts.message_id`,
  hoặc snowflake suy từ thời điểm gửi cho attempt cũ), ghép reply theo **người nhận (`<@!id>`) + số điểm (chịu dấu
  phẩy nghìn) + success_pattern** ⇒ item `success` + ghi sổ `reward_distributions` (không gửi lại ⇒ không cộng đôi).
  Quét hết kênh quá `REWARD_RECONCILE_GRACE_S` (45s) mà không có reply ⇒ `reward_items.reconcile_result='no_reply'`
  (migration `003_reward_reconcile.sql`) = bằng chứng cho phép gửi lại. Kiểm trên dữ liệu thật (chỉ đọc): job 2 → 7/7,
  job 4 → 17/17 item unknown tìm được reply thành công.
- **Runner** đối soát: trước khi kết luận chuỗi unknown, và lần cuối trước khi job completed/paused (chờ đủ grace).
  Chuỗi unknown tính theo token (≥ngưỡng ⇒ loại token) và toàn job (≥2×ngưỡng ⇒ `auto_pause`).
- **API**: `POST /jobs/{id}/reconcile`, `POST /jobs/{id}/retry-unknown` (đối soát → mở lại `no_reply` → job
  completed chuyển `paused` để Tiếp tục). `resolve → pending` trên job completed cũng chuyển `paused`. UI JobDetailPage:
  nút "Đối soát" + "Gửi lại item không rõ".
- **Giảm unknown tại nguồn**: lỗi Gateway/`op8` của token ⇒ `session_error` (chưa gửi) ⇒ requeue + token nghỉ, không còn
  thành `unknown`/`USER_NOT_FOUND` oan; 403/404 chỉ loại token trong lần chạy (chỉ 401 ghi `invalid` vào DB);
  `mark_sent` lưu neo `message_id`.
- **`engine/token_pool.py`**: chọn token ngẫu nhiên (không lặp token vừa dùng), nhịp chung ngẫu nhiên, nhịp riêng token.
