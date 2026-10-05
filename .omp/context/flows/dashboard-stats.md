# Flow: Đọc Dashboard / Thống kê (Viewer)
<!-- Nguồn: admin-web/src/routes/DashboardPage.tsx → admin-server/{viewer.py,cache.py,db.py,stats_core.py} → (dev) DATA_DIR JSON của Tool | (production) Postgres `documents` do upload-facebook/server/ ghi -->

## Entry Points

| Method | Path | File | Middleware |
|---|---|---|---|
| GET | `/v1/viewer/summary` | `admin-server/viewer.py summary` | Bearer JWT |
| GET | `/v1/viewer/traffic` | `admin-server/viewer.py traffic` | Bearer JWT |
| GET | `/v1/viewer/top-channels` | `admin-server/viewer.py top_channels` | Bearer JWT |
| GET | `/v1/viewer/views-by-country` | `admin-server/viewer.py views_by_country` | Bearer JWT |
| GET | `/v1/viewer/country-tags` | `admin-server/viewer.py country_tags` | Bearer JWT |
| GET | `/v1/viewer/metrics` | `admin-server/viewer.py metrics` | Bearer JWT (trả list metric cố định hardcode) |
| POST/GET/DELETE | `/v1/viewer/links*` | `admin-server/links.py` | Bearer JWT (trừ `/redeem`: public, rate-limit) |
| POST/GET/PATCH/DELETE | `/v1/viewer/groups*` | `admin-server/groups.py` | Bearer JWT |

**Thêm 2026-09-30**: 5 endpoint viewer nhận thêm query param `owner_account_id` (chỉ có tác dụng với
role `admin`/`dash_admin`) và `group_id` (mọi role) — xem bước 2.

## Luồng chính

1. **Frontend gọi** — `admin-web/src/routes/DashboardPage.tsx` → `admin-web/src/api.ts
   viewerApi.summary/traffic/topChannels/viewsByCountry/countryTags/metrics` với params
   `date_from,date_to,country,scope,limit,owner_account_id,group_id`.

2. **Route decode JWT + resolve PHẠM VI DỮ LIỆU** — `viewer.py require_user` chỉ decode JWT (KHÔNG
   tra DB/suspended, khác `auth.get_current_user`); `viewer.py _resolve_scope` (MỚI) mới là nơi
   quyết định thấy gì:
   - role `admin`/`dash_admin`: mặc định `user_ids=None` (xuyên user, hành vi cũ); nếu có
     `owner_account_id` → `user_ids = db.linked_user_ids(owner_account_id)` (soi đúng 1 nhân sự).
   - role khác (`viewer`,`discord`,...): LUÔN `user_ids = db.linked_user_ids(sub)` — chưa liên kết
     Tool nào (`account_links`, xem `links.py`) → `user_ids=[]` → dữ liệu rỗng, `summary` trả thêm
     `linked:false` để FE hiện banner hướng dẫn.
   - `group_id` (mọi role) → `channel_refs = db.group_channel_refs(group_id)` (`groups.py`), thu hẹp
     thêm theo kênh cụ thể trong group.
   - Gọi `db.<method>(..., user_ids, channel_refs)` (đối tượng `db` ở đây LÀ `CachedDb`, bọc ngoài
     cùng `main.py`).

3. **CachedDb kiểm cache** — `admin-server/cache.py` (method nằm trong `_CACHED` tuple)
   - `_version()`: GET Redis key `adm:v2:docver`; hết hạn (`CACHE_VERSION_TTL=300s`) thì gọi
     `inner.doc_version()` để lấy version mới.
   - `_key()`: `f"adm:{SCHEMA}:{ver}:{method}:{sha1(params_sorted)[:16]}"` — params nay gồm cả
     `user_ids`/`channel_refs` (sort thành tuple trước khi hash) nên 2 scope khác nhau KHÔNG đụng
     cache của nhau.
   - Redis GET key:
     - **HIT** → trả JSON cache thẳng.
     - **MISS** → chiếm `asyncio.Lock` theo key (single-flight, tránh nhiều request cùng tính lại) →
       gọi `inner.<method>(...)` (Db gốc, KHÔNG qua cache) → `Cache.set_json(key, out, CACHE_TTL=3600s)`
       → trả kết quả.
   - `CACHE_ENABLED=0` hoặc thiếu `REDIS_URL` → bỏ qua toàn bộ bước cache, gọi thẳng `inner`.


4. **`inner.<method>` rẽ nhánh theo `MODE`** (`admin-server/db.py`):

   **Nhánh `dev` — `LocalFileDb`** (kế thừa `SqliteDb`):
   - Đọc thẳng JSON trong `DATA_DIR` (PHẢI khớp `APP_DATA_DIR` của Tool):
     `fb_posts.json`, `yt_insights_daily.json`, `repost_events.json`, `repost_pairings.json`,
     `repost_groups.json`, `fb_page_meta.json`/`fb_pages.json`, `yt_channels.json`.
   - `doc_version()` = sha1 của `(mtime_ns, size)` của 8 file JSON — đổi khi Tool/Electron ghi đè file.
   - **NHẬN nhưng BỎ QUA `user_ids`**: 1 thư mục JSON = 1 Tool, không có khái niệm nhiều user — cô
     lập theo Tool chỉ có ý nghĩa ở `PostgresDb`/`SqliteDb` (dev mode giữ nguyên hành vi single-tenant
     cũ, có chủ ý). `channel_refs` (lọc theo group) VẪN áp dụng bình thường ở dev.

   **Nhánh `production` — `PostgresDb`**:
   - `SELECT payload FROM documents WHERE store='<tên store>' AND NOT deleted [AND user_id =
     ANY($1::text[])]` — thêm điều kiện `user_id` khi `user_ids is not None` (MỚI 2026-09-30). Bảng
     `documents` do **sync server của Tool** (`upload-facebook/server/`) ghi, admin-server chỉ SELECT.
   - `doc_version()`: `SELECT COALESCE(SUM(next_rev),0) FROM revs` — tăng mỗi khi sync server ghi
     document mới cho BẤT KỲ user nào (con dấu vẫn TOÀN CỤC, không theo user — filter theo user chỉ
     nằm ở bước đọc `documents`, không ảnh hưởng độ tươi cache).

   **Nhánh `SqliteDb`** (test/import, `db.py:483-579`): giống Postgres nhưng đọc SQLite (`import_local_data.py` nạp vào để giả lập).

5. **Tính KPI** — cả 3 nhánh gọi CHUNG `stats_core.{summary,traffic_metric,top_channels,
   views_by_country,country_tags}` — module thuần byte-identical với
   `upload-facebook/app/analytics/stats_core.py`, đảm bảo Tool và Admin ra cùng số liệu dù đọc 2 nguồn
   khác nhau. `channel_refs` (lọc group) áp dụng TRƯỚC bước này bằng `db.py::_apply_channel_refs`
   (thu hẹp `fb_posts`/`yt_daily`/`channel_meta`/`target_country`) — vì chỉ 2/5 hàm `stats_core` có
   tham số `yt_filter`/`fb_filter`, lọc trước input là cách áp dụng thống nhất mà KHÔNG sửa
   `stats_core.py`.

6. **Set cache + trả về** — `CachedDb` ghi kết quả vào Redis (TTL 3600s), trả JSON cho frontend.

7. **Render** — `DashboardPage.tsx` vẽ biểu đồ bằng `recharts`.

## Ép làm mới ngay (bỏ qua TTL)

`POST /v1/admin/cache/flush` (`admin_api.py:148`, role=admin) → `Cache.invalidate_all()`
(`cache.py:118-137`) — SCAN theo lô + UNLINK toàn bộ key `adm:*` (kể cả version key) → lần đọc kế tiếp
tính lại ngay, không chờ `CACHE_VERSION_TTL`. Frontend gọi từ `AdminPage.tsx`/`DashboardPage.tsx` (nút
"Làm mới cache", import xác nhận nhưng vị trí gọi cụ thể trong `DashboardPage.tsx` [chưa xác định dòng
chính xác]).

## Xử lý lỗi

| Điều kiện | Response |
|---|---|
| JWT invalid/hết hạn | 401 (frontend tự refresh — xem flow login-auth) |
| Redis lỗi kết nối | fail-open: log warning, tắt cache, đọc thẳng DB (không lỗi cho client) |
| `MODE=dev` nhưng `DATA_DIR` sai/rỗng | Mọi số liệu trả về rỗng/0 — KHÔNG phải lỗi code, là sai cấu hình (bài học từ Tool docs) |
| Postgres bảng `documents`/`revs` chưa tồn tại (CSDL sạch) | `doc_version()` trả `"na"` |

## Side Effects

- Ghi Redis (cache kết quả + version key), KHÔNG ghi Postgres/JSON (route hoàn toàn đọc).

## Dependencies

- Module: `admin-server/{viewer.py,links.py,groups.py,cache.py,db.py,stats_core.py}`.
- Database tables (đọc): `documents`, `revs` (production, sở hữu bởi upload-facebook/server/).
- Database tables (đọc/ghi, admin-server sở hữu): `account_links`, `link_codes`, `channel_groups`,
  `channel_group_members` — xem `data-models.md` mục 1.
- File (đọc, dev): 7 file JSON trong `DATA_DIR` — xem `data-models.md` mục 3.
- Phụ thuộc chéo repo: `stats_core.py` PHẢI giữ byte-identical với
  `upload-facebook/app/analytics/stats_core.py` — đổi 1 bên mà quên bên kia sẽ làm 2 hệ thống ra số
  liệu khác nhau âm thầm (không có lỗi runtime).
