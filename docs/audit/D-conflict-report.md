# D. Conflict & Bug Report

---

## D.1 Bug #1 — Gateway Routing: `/v1/admin/permissions` → 404

| Thuộc tính | Giá trị |
|---|---|
| **Loại** | Bug: Route missing in gateway config |
| **Mức độ** | HIGH |
| **Service** | api-gateway, identity-service |
| **Module** | `infra/service-registry.json`, `infra/gateway/` |
| **Ảnh hưởng** | `PermissionsDialog.tsx` nhận 404 → dùng FALLBACK_CATALOG (thiếu capability:*) |

### Nguyên nhân gốc
`infra/service-registry.json` khai báo `public_prefixes` của identity-service:
```json
"public_prefixes": ["/v1/auth", "/v1/accounts", "/v1/admin/accounts", "/.well-known", "/oauth"]
```
Thiếu `/v1/admin/permissions`. Gateway (render_nginx.py) sinh route từ `public_prefixes` → path này không được route → 404.

### Bằng chứng
```
# Via gateway (all roles):
GET /v1/admin/permissions → 404

# Direct to identity-service:
GET http://127.0.0.1:57962/v1/admin/permissions → 401 (route exists)
```

### Hệ quả thực tế
Khi admin mở `PermissionsDialog`, `permissionsApi.catalog()` gọi `/v1/admin/permissions` nhận 404, React catch fallback về `FALLBACK_CATALOG`:
```ts
const FALLBACK_CATALOG: PermissionCatalogEntry[] = [
  { name: "reward:access", ... },
  { name: "reward:admin", ... },
];
```
Catalog này KHÔNG có `capability:web_access`, `capability:tool_integration`, etc. → Admin vẫn CÓ THỂ cấp capabilities (vì PUT/DELETE `/v1/admin/accounts/{id}/permissions/{perm}` hoạt động đúng), nhưng UI không hiện danh sách đầy đủ để tích vào.

**Sau khi FeatureCodeAudit + TenseTrout đã extend FALLBACK_CATALOG trong `PermissionsDialog.tsx` để có ALL_CAPS**, impact giảm xuống: UI vẫn hiển thị đúng capability nhờ FALLBACK_CATALOG mới. Nhưng catalog từ server (có description đầy đủ, admin_only flag) vẫn không load được → vẫn cần sửa.

### Fix
```json
// infra/service-registry.json, public_prefixes của identity-service:
"/v1/admin/permissions"  // Thêm dòng này
```
Sau đó chạy: `python infra/gateway/render_nginx.py`

---

## D.2 Bug #2 — TTL Access Token Chưa Đúng Thiết Kế

| Thuộc tính | Giá trị |
|---|---|
| **Loại** | Configuration deviation from design |
| **Mức độ** | Medium |
| **File** | `services/identity-service/src/identity_service/config.py` |
| **Thiết kế** | D12: 600s (10 phút) |
| **Thực tế** | 900s (15 phút) |

### Nguyên nhân
Giá trị default chưa được đổi. Có thể sửa qua env `IDENTITY_ACCESS_TOKEN_TTL_S=600` nhưng cần cập nhật cả test parity (đang kiểm tra 900s).

### Hệ quả
Window rủi ro khi token bị chiếm tăng thêm 5 phút. Với refresh token xoay một chiều, tác động bị giới hạn.

---

## D.3 Bug #3 — CORS Origins Hardcoded

| Thuộc tính | Giá trị |
|---|---|
| **Loại** | Configuration: production-blocking |
| **Mức độ** | High (khi deploy) |
| **File** | `services/identity-service/src/identity_service/main.py` |

### Nguyên nhân
CORS `allow_origins` hardcode danh sách `localhost` dev ports. Production domain chưa được thêm, sẽ block CORS khi deploy.

### Fix
Đọc từ env `IDENTITY_CORS_ORIGINS` (danh sách phân cách dấu phẩy).

---

## D.4 Conflict #1 — Test Suite vs Dev Stack (DB Deadlock)

| Thuộc tính | Giá trị |
|---|---|
| **Loại** | Environment conflict |
| **Mức độ** | High (ảnh hưởng CI reliability) |
| **Scope** | `tests/contract`, `tests/e2e`, `services/identity-service/tests/` |

### Mô tả
Khi dev stack đang chạy song song, các test suite thi nhau dùng cùng Postgres → deadlock ở `FOR UPDATE` / `TRUNCATE`. Kết quả:
- `tests/contract`: FLAKY (đôi khi 40 pass, đôi khi 2 error)
- `services/identity-service` test: `test_feed_consumer.py` 9/19 failed vì `oidc_cursor` lock bởi dev stack
- `tests/e2e`: 16/31 error (fixture timeout 30s chờ `account.verified`)

### Fix
Một trong hai:
- Dừng dev stack trước khi chạy test (`python infra/scripts/dev_stack.py down`)
- Dùng `DevStack.reset_data()` với DB riêng hoặc Docker compose isolated environment

---

## D.5 Conflict #2 — E2E Test Hardcoded Platform Name

| Thuộc tính | Giá trị |
|---|---|
| **Loại** | Test code violation |
| **Mức độ** | Low (test env only) |
| **File** | `apps/manager-web/src/routes/DashboardPage.test.tsx` |
| **Rule** | "Không hardcode tên Tool" (project convention) |

### Mô tả
Test dùng chuỗi `"toolx_social"` làm platform mock — vi phạm convention nhất quán của project về tên Tool. Gây 1 test fail trong E2E suite.

---

## D.6 Missing Test Coverage — Luồng Mới

| Thuộc tính | Giá trị |
|---|---|
| **Loại** | Test gap |
| **Mức độ** | High (không có regression protection) |

### 4 luồng không có test:

| Luồng | Endpoint/Handler | Hệ quả nếu bug |
|---|---|---|
| Machine resource listing (fallback_tool sees all) | `GET /v1/integrations/resources` | Có thể lộ data của user khác |
| Machine user listing | `GET /v1/integrations/resource-owners` | Có thể lộ user list |
| Auto-provision trên permission grant | `_permission_changed()` | Client không được tạo silently |
| `require_tool_integration` → 403 | `deps.py` | Capability bypass nếu check bị xóa |

**Đây là rủi ro bảo mật cao nhất được phát hiện** — các endpoint liên quan đến data isolation giữa owners không có test tự động.

---

## D.7 Conflict #3 — `read_resources` PK Thiếu `resource_type` (M3)

| Thuộc tính | Giá trị |
|---|---|
| **Loại** | Data integrity gap |
| **Mức độ** | Medium |
| **File** | analytics migration `0002` |
| **Source** | Phát hiện lần đầu trong audit 2026-10-08, chưa fix |

### Mô tả
PK của `read_resources` trong analytics-service chỉ dùng `(owner_account_id, platform, external_id)` mà không có `resource_type`. Kết quả: nếu cùng `external_id` xuất hiện với hai `resource_type` khác nhau (ví dụ: `page` và `channel`), bản ghi sau ghi đè bản ghi trước.

### Hệ quả
Dữ liệu analytics có thể bị sai nếu Tool gửi cùng `external_id` với `resource_type` khác. Hiện tại ít xảy ra nhưng là time bomb.

---

## D.8 Tóm tắt ưu tiên xử lý

| # | Bug/Conflict | Mức | Fix |
|---|---|---|---|
| 1 | Gateway routing `/v1/admin/permissions` | HIGH | Thêm prefix vào service-registry.json |
| 2 | Missing test 4 luồng mới | HIGH | Viết test integration/contract |
| 3 | CORS hardcoded | HIGH | Đọc từ env |
| 4 | E2E DB deadlock với dev stack | HIGH | Env isolation |
| 5 | TTL access token 900s | Medium | Env var + sửa test parity |
| 6 | E2E hardcoded platform name | Low | Sửa test string |
| 7 | `read_resources` PK thiếu resource_type | Medium | Schema migration (breaking) |
| 8 | `agg_daily` chưa implement | Low | Backlog |
