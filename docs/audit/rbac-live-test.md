# RBAC Live Test — Permission Matrix

Ngày thực hiện: 2026-10-14
Phương pháp: Gửi HTTP request thật bằng Python `urllib` (không curl/fetch) tới stack dev đang chạy
(gateway `http://127.0.0.1:57965`, identity `http://127.0.0.1:57962`, integration
`http://127.0.0.1:57963`). Không ghi/sửa bất kỳ dữ liệu nào — mọi test gây escalation chỉ dùng token
*không đủ quyền* nên request bị chặn ở tầng auth trước khi chạm business logic/DB.

## 1. JWT payload từng tài khoản demo

Đăng nhập qua `POST /v1/auth/login` (gateway → identity-service), giải mã phần payload JWT (base64url,
không cần verify chữ ký vì chỉ đọc claim).

### demoadmin (role=admin)
```json
{
  "iss": "http://127.0.0.1:57962",
  "aud": ["manager-api"],
  "sub": "15c17ac05872d578bcf7a195281417ed",
  "username": "demoadmin",
  "role": "admin",
  "perms": [
    "capability:api_read", "capability:api_write", "capability:dashboard_access",
    "capability:manual_channel_mgmt", "capability:multi_tool",
    "capability:tool_integration", "capability:web_access"
  ],
  "client_type": "web"
}
```
→ **admin có đủ 7 capability** (toàn bộ danh mục hiện có), khớp kỳ vọng "role admin không ngầm định
capability nhưng được seed đủ tường minh qua migration 0005" (ghi trong `docs/architecture/
capability-model-v1.md`).

### demoowner (role=viewer)
```json
{
  "sub": "2b522c93e7b50c7c00c6cd38a03710d5",
  "username": "demoowner",
  "role": "viewer",
  "perms": ["capability:dashboard_access", "capability:web_access"]
}
```
→ viewer **CHỈ** có 2 capability mặc định (`web_access`, `dashboard_access`). **KHÔNG** có
`capability:tool_integration` — xác nhận bằng test thực tế bên dưới (mục 6).

### rewarduser (role=discord)
```json
{
  "sub": "b493ebd1bc247f7afbdc6616582e0681",
  "username": "rewarduser",
  "role": "discord",
  "perms": ["reward:access"]
}
```
→ discord chỉ có `reward:access`, không có bất kỳ `capability:*` nào.

### Ghi chú TTL access token
`exp - iat` đo được = **900 giây** cho cả 3 token (demoadmin, demoowner, rewarduser), khớp phát hiện cũ
trong audit 2026-10-08 ("TTL access token: nên là 600s nhưng thực tế là 900s") — **vẫn chưa được sửa**
tính đến thời điểm test này.

## 2. Ma trận quyền (Permission Matrix)

| Endpoint | Yêu cầu | demoadmin | demoowner (viewer) | rewarduser (discord) | anonymous | Kết luận |
|---|---|---|---|---|---|---|
| `GET /v1/admin/users` | admin/dash_admin | **200** | **403** | **403** | **401** | PASS |
| `GET /v1/admin/keys` | admin/dash_admin | **200** | **403** | **403** | **401** | PASS |
| `GET /v1/integrations/clients` | bất kỳ user đã login (list chỉ thấy resource của chính mình; admin thấy tất cả) | 200 (thấy client đã có) | 200 (danh sách rỗng) | 200 (danh sách rỗng) | 401 | PASS — **không cần** `capability:tool_integration` để *xem* danh sách (chỉ cần để *tạo*, xem mục 6) |
| `GET /v1/ops/users` | admin/dash_admin (`OPS_ROLES`) | **200** | **403** | **403** | **401** | PASS |
| `GET /v1/ops/inventory` | admin/dash_admin | **200** | **403** | **403** | **401** | PASS |
| `GET /v1/analytics/summary` | viewer trở lên | 200 | 200 | 200 (mọi role login đều viewer+) | 401 | PASS |
| `GET /v1/viewer/links` | bất kỳ user đã login | 200 | 200 | 200 | 401 | PASS |
| `GET /v1/admin/permissions` (permission catalog) | admin only | **404** | **404** | **404** | **404** | **FAIL — xem Phát hiện #1 bên dưới** |
| `GET /health/ready` | public | 200 (không cần token) | — | — | — | PASS |
| `GET /.well-known/jwks.json` | public | 200 (không cần token) | — | — | — | PASS |

Raw evidence chọn lọc:
- `GET /v1/admin/users` as demoadmin → `200 {"users":[{"id":"b493ebd1bc247f7afbdc6616582e0681","username":"rewarduser",...}]}`
- `GET /v1/admin/users` as demoowner → `403 {"detail":"Chỉ quản trị viên mới có quyền này"}`
- `GET /v1/ops/users` as rewarduser → `403 {"detail":"Chỉ admin/dash_admin được truy cập"}`
- `GET /v1/admin/permissions` as demoadmin (admin thật, đủ quyền) → `404 {"ok":false,"error":{"code":"NOT_FOUND","message":"Not Found","details":{}}}`

### ⚠️ Phát hiện #1 (MỨC CAO — gateway routing gap, không phải lỗi RBAC)

`GET /v1/admin/permissions` (permission catalog, `services/identity-service/.../permissions/router.py:16`,
dependency `require_admin`) trả **404 cho MỌI role kể cả admin thật**, khi gọi qua gateway
(`http://127.0.0.1:57965`). Nguyên nhân xác nhận bằng code: `infra/service-registry.json` khai báo
`public_prefixes` của `identity-service` là:
```
["/v1/auth", "/v1/accounts", "/v1/admin/accounts", "/.well-known", "/oauth"]
```
— **không có** `/v1/admin/permissions` và cũng không có prefix tổng quát `/v1/admin`. Gateway
(`services/api-gateway/src/api_gateway/routing/table.py`, hàm `resolve()`) khớp tiền tố tại ranh giới
segment; vì không có rule nào khớp `/v1/admin/permissions` (segment `permissions` khác `accounts`,
`keys`, `users`, `cache`, `country-canonicals`, `country-aliases` — các rule cụ thể duy nhất dưới
`/v1/admin/*`), request rơi vào 404 trước khi tới identity-service. Kiểm chứng: gọi **trực tiếp** tới
identity-service (`http://127.0.0.1:57962/v1/admin/permissions`, không qua gateway) với anonymous →
`401 {"detail":"Thiếu token"}` (route **CÓ tồn tại** ở service, bug chỉ nằm ở tầng gateway).

So sánh: `PUT /v1/admin/accounts/{id}/permissions/{perm}` (cùng router, cùng file) hoạt động bình thường
qua gateway (403 đúng cho non-admin) vì prefix `/v1/admin/accounts` đã được đăng ký — chỉ riêng
endpoint catalog `/v1/admin/permissions` (không có `/accounts/{id}` ở giữa) bị rơi ra ngoài routing
table.

**Tác động**: Admin frontend/API client không thể gọi permission catalog qua gateway production (chỉ
hoạt động nếu gọi thẳng identity-service, vi phạm kiến trúc "mọi truy cập qua gateway"). Cần thêm
`/v1/admin/permissions` vào `infra/service-registry.json` (`public_prefixes` của `identity-service`) hoặc
thêm rule trong `infra/gateway/route-map.json`.

## 3. Kịch bản leo thang đặc quyền (Privilege Escalation)

| Kịch bản | Request | Kết quả | Đánh giá |
|---|---|---|---|
| viewer gọi admin endpoint | `GET /v1/admin/users` as demoowner | 403 | Chặn đúng |
| discord gọi ops endpoint | `GET /v1/ops/users` as rewarduser | 403 `"Chỉ admin/dash_admin được truy cập"` | Chặn đúng |
| viewer tự tạo invite-key role=admin | `POST /v1/admin/keys {"role":"admin"}` as demoowner | 403 `"Chỉ quản trị viên mới có quyền này"` | Chặn đúng (request bị chặn ở `require_admin` TRƯỚC khi chạm business logic tạo key — không có dữ liệu nào bị ghi) |
| discord tự tạo invite-key role=admin | `POST /v1/admin/keys {"role":"admin"}` as rewarduser | 403 | Chặn đúng |
| viewer tự cấp quyền `reward:access` cho chính mình | `PUT /v1/admin/accounts/{own_id}/permissions/reward:access` as demoowner | 403 `"Chỉ quản trị viên mới có quyền này"` | Chặn đúng — endpoint cấp quyền chỉ chấp nhận `require_admin`, không phân biệt "cấp cho chính mình hay người khác" nên không có đường vòng |
| discord thử tương tự | `PUT /v1/admin/accounts/{own_id}/permissions/reward:access` as rewarduser | 403 | Chặn đúng |
| viewer tạo Integration Client dù thiếu capability | `POST /v1/integrations/clients {...}` as demoowner | **403** `{"code":"SCOPE_DENIED","message":"Tài khoản chưa được cấp quyền sử dụng Tool tích hợp"}` | Chặn đúng — `require_tool_integration` (integration-service `deps.py:42`) kiểm tra `capability:tool_integration` trong `perms`, viewer không có nên bị từ chối |
| discord tạo Integration Client | tương tự, as rewarduser | 403 `SCOPE_DENIED` | Chặn đúng |

### IDOR (Insecure Direct Object Reference)

| Request | Kết quả | Đánh giá |
|---|---|---|
| `GET /v1/accounts/{own_sub}/permissions` as demoowner (xem quyền của chính mình) | 200 `{"account_id":"...","permissions":["capability:dashboard_access","capability:web_access"]}` | Hợp lệ |
| `GET /v1/accounts/{demoadmin_sub}/permissions` as demoowner (**IDOR** — cố xem quyền người khác) | **403** `{"detail":"Chỉ xem được quyền của chính mình"}` | **Chặn đúng** — code `services/identity-service/src/identity_service/permissions/service.py:38-40`: `if actor.sub != account_id and actor.role not in ("admin","dash_admin"): raise 403` |
| `GET /v1/accounts/{demoowner_sub}/permissions` as demoadmin (admin xem quyền người khác — hợp lệ theo thiết kế) | 200 | Đúng thiết kế (admin có quyền giám sát) |

**Không phát hiện lỗ hổng IDOR** ở endpoint này — kiểm tra ownership được thực hiện tường minh trong
tầng service, không chỉ dựa vào route.

## 4. Phân tách Machine auth vs User auth

| Request | Kết quả | Đánh giá |
|---|---|---|
| `POST /v1/integrations/jobs/claim` dùng **user JWT** (demoadmin, role=admin) thay vì `mpk_` API key | **401** `{"code":"AUTH_INVALID","message":"Thiếu hoặc sai API key"}` | **Chặn đúng** — endpoint này thuộc `machine_router` (`services/integration-service/.../jobs/router.py`), yêu cầu xác thực bằng API key máy (`mpk_<key_id>.<secret>`), **không** chấp nhận Bearer JWT dù là admin. Xác nhận user-plane và machine-plane **không thể hoán đổi cho nhau** kể cả bằng quyền admin cao nhất. |
| `GET /internal/v1/feed` qua gateway, không có header gì | **404** | Gateway chặn mọi path bắt đầu bằng `/internal` ngay ở tầng chuẩn hoá path (`services/api-gateway/src/api_gateway/routing/table.py:76-77`: `if prefix.strip("/").split("/")[0].lower()=="internal": raise RouteConfigError` — route `/internal/*` **không bao giờ** được khai báo trong route table nên luôn 404) |
| `GET /internal/v1/feed` qua gateway, kèm **Bearer JWT admin thật** | **404** (y hệt, không có auth nào cứu được) | Xác nhận: route nội bộ **không định tuyến được từ bên ngoài dù có JWT hợp lệ**, đúng thiết kế "nội bộ không qua gateway, không nằm trong OpenAPI công khai" |

## 5. Capability permissions — kiểm chứng trực tiếp

- JWT của **viewer** (demoowner) chỉ chứa `capability:web_access`, `capability:dashboard_access` — **không**
  có `capability:tool_integration`, `capability:manual_channel_mgmt`, `capability:api_read`,
  `capability:api_write`, `capability:multi_tool`.
- JWT của **discord** (rewarduser) chỉ chứa `reward:access` — **không** có bất kỳ `capability:*` nào.
- JWT của **admin** (demoadmin) chứa đủ cả 7 capability hiện có trong hệ thống (migration 0005 seed đủ
  cho role admin, khớp tài liệu kiến trúc).
- Thử `POST /v1/integrations/clients` (tạo Integration Client, yêu cầu `capability:tool_integration`)
  bằng token viewer và token discord → **cả hai đều bị từ chối 403 `SCOPE_DENIED`** đúng như kỳ vọng vì
  JWT không mang capability này.
- `GET /v1/integrations/clients` (chỉ **xem** danh sách) **không** yêu cầu `capability:tool_integration`
  — mọi user đã login đều gọi được (viewer/discord nhận về danh sách rỗng vì chưa sở hữu client nào,
  không phải do bị chặn quyền). Đây là hành vi đúng theo thiết kế (phân biệt "xem" và "tạo/quản lý").

## 6. Tổng kết

| Hạng mục | Kết quả |
|---|---|
| Admin-only endpoints (`/v1/admin/users`, `/v1/admin/keys`, `/v1/ops/*`) | **PASS** — chặn đúng viewer/discord/anonymous |
| Capability gate cho Integration Client creation | **PASS** — `SCOPE_DENIED` 403 đúng cho role thiếu capability |
| IDOR trên `/v1/accounts/{id}/permissions` | **PASS** — ownership check tường minh ở service layer |
| Machine vs User auth separation (`jobs/claim`) | **PASS** — user JWT không thay thế được machine API key |
| `/internal/*` bị chặn ở gateway | **PASS** — 404 bất kể có JWT hay không |
| Permission self-escalation (viewer/discord tự cấp quyền admin/reward) | **PASS** — `require_admin` chặn tuyệt đối, không có đường vòng qua "cấp cho chính mình" |
| `GET /v1/admin/permissions` qua gateway | **FAIL (routing gap)** — 404 cho mọi role kể cả admin hợp lệ, do thiếu khai báo prefix trong `infra/service-registry.json`/`route-map.json`. Route tồn tại và hoạt động đúng khi gọi thẳng identity-service. |
| TTL access token | Vẫn 900s (chưa sửa về 600s theo khuyến nghị audit trước) |

**Không phát hiện lỗ hổng leo thang đặc quyền hay IDOR nào mới.** Toàn bộ các đường tấn công được thử
(tự cấp role admin, tự cấp capability/permission, đọc quyền người khác, dùng JWT user thay machine key,
truy cập route nội bộ từ bên ngoài) đều bị chặn đúng ở tầng dependency injection (`require_admin`,
`require_ops`, `require_tool_integration`, `require_full_admin`) hoặc tầng service (`PermissionService.
list_for` kiểm ownership). Phát hiện duy nhất là **lỗi cấu hình định tuyến gateway** (không phải lỗ hổng
bảo mật — ngược lại, nó khiến endpoint catalog quyền admin tạm thời không truy cập được qua đường chính
thức), cần vá bằng cách thêm prefix vào registry.
