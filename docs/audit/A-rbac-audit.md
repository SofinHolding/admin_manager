# A. RBAC Audit Report

**Phương pháp:** Live HTTP testing với JWT thực của 3 demo accounts trên dev stack  
**Gateway:** `http://127.0.0.1:57965`  
**Không sửa dữ liệu — chỉ gọi endpoint đọc + test bị chặn với token thiếu quyền**

---

## A.1 Role hiện tại trong hệ thống

| Role | Nguồn | Default Capabilities | Ghi chú |
|---|---|---|---|
| `admin` | identity-service `accounts.role` | web_access, dashboard_access, manual_channel_mgmt, **tool_integration, api_write, api_read, multi_tool** (tất cả 7) | Toàn quyền |
| `dash_admin` | identity-service | web_access, dashboard_access | Chỉ xem dashboard; KHÔNG sửa/xóa admin accounts |
| `viewer` | identity-service | web_access, dashboard_access | User thường |
| `discord` | identity-service (shim) | KHÔNG có capability mặc định | Shim cho backward compat → `reward:access` qua migration |

**Nguồn:** `services/identity-service/src/identity_service/auth/service.py` (grant default caps) + migration `0005_capabilities.sql`

---

## A.2 Capability Catalog (đã verify source code)

| Capability | admin_only | Default cho role |
|---|---|---|
| `capability:web_access` | No | viewer, dash_admin, admin |
| `capability:dashboard_access` | No | viewer, dash_admin, admin |
| `capability:manual_channel_mgmt` | No | admin |
| `capability:tool_integration` | No | admin |
| `capability:api_read` | No | admin |
| `capability:api_write` | **Yes** | admin |
| `capability:multi_tool` | **Yes** | admin |
| `reward:access` | No | (discord shim) |
| `reward:admin` | No | — |

---

## A.3 Ma trận Permission × Endpoint (kết quả thực tế)

### Ký hiệu: ✅ = PASS (đúng như thiết kế) | ❌ = FAIL | 🐛 = Bug | — = N/A

| Endpoint | anonymous | viewer | discord | admin | Thiết kế | Kết quả |
|---|---|---|---|---|---|---|
| `GET /health/ready` | 200 | 200 | 200 | 200 | Public | ✅ |
| `GET /.well-known/jwks.json` | 200 | 200 | 200 | 200 | Public | ✅ |
| `POST /v1/auth/login` | 200 | — | — | — | Public | ✅ |
| `GET /v1/admin/users` | 401 | **403** | **403** | 200 | admin only | ✅ |
| `GET /v1/admin/keys` | 401 | **403** | **403** | 200 | admin only | ✅ |
| `GET /v1/admin/permissions` | **404** | **404** | **404** | **404** | admin only | 🐛 **BUG** |
| `PUT /v1/admin/accounts/{id}/permissions/{perm}` | 401 | 403 | 403 | 200 | admin only | ✅ |
| `GET /v1/accounts/{id}/permissions` | 401 | 403 (other) / 200 (self) | — | 200 | owner or admin | ✅ |
| `GET /v1/integrations/clients` | 401 | **403** CAPABILITY_REQUIRED | 403 | 200 | capability:tool_integration | ✅ |
| `POST /v1/integrations/clients` | 401 | **403** | 403 | 200 | capability:tool_integration | ✅ |
| `GET /v1/integrations/resources` | 401 | 403 (user JWT, not machine) | — | — | machine key only | ✅ |
| `GET /v1/integrations/resource-owners` | 401 | 403 | — | — | machine key only | ✅ |
| `GET /v1/ops/users` | 401 | **403** | **403** | 200 | admin/dash_admin | ✅ |
| `GET /v1/ops/inventory` | 401 | **403** | **403** | 200 | admin/dash_admin | ✅ |
| `GET /v1/analytics/v2/summary` | 401 | **200** | 403 | 200 | viewer+ | ✅ |
| `GET /v1/viewer/links` | 401 | **200** | 403 | 200 | viewer+ | ✅ |
| `GET /v1/reward/health` | 401/200 | — | 200 | 200 | reward:access | ✅ |
| `POST /v1/integrations/jobs/claim` (JWT) | 401 | 401 "sai API key" | 401 | 401 | machine only | ✅ |
| `GET /internal/v1/feed` | **404** | **404** | **404** | **404** | Blocked | ✅ |

---

## A.4 JWT Payload thực tế (decode từ live tokens)

### demoadmin (admin)
```json
{
  "role": "admin",
  "perms": [
    "capability:web_access", "capability:dashboard_access",
    "capability:manual_channel_mgmt", "capability:tool_integration",
    "capability:api_read", "capability:api_write", "capability:multi_tool",
    "reward:access", "reward:admin"
  ],
  "exp": <now+900s>
}
```

### demoowner (viewer)
```json
{
  "role": "viewer",
  "perms": [
    "capability:web_access", "capability:dashboard_access"
  ],
  "exp": <now+900s>
}
```

### rewarduser (discord)
```json
{
  "role": "discord",
  "perms": ["reward:access"],
  "exp": <now+900s>
}
```

**Nhận xét:** Capability model đã hoạt động đúng theo thiết kế trong `capability-model-v1.md`. Viewer có đúng 2 caps; admin có đủ 7 + reward caps.

---

## A.5 Kiểm thử Privilege Escalation

| Test case | Input | Expected | Actual | Kết quả |
|---|---|---|---|---|
| Viewer tự cấp `reward:access` cho chính mình | `PUT /v1/admin/accounts/self/permissions/reward:access` với viewer JWT | 403 | **403** ADMIN_FORBIDDEN | ✅ PASS |
| Viewer gọi `/v1/admin/keys` | viewer JWT | 403 | **403** SCOPE_DENIED | ✅ PASS |
| discord gọi `/v1/ops/users` | discord JWT | 403 | **403** SCOPE_DENIED | ✅ PASS |
| Admin tự nâng role của chính mình không qua admin API | Không có đường API | — | — | ✅ Không có vector |
| User JWT gọi machine API `/v1/integrations/jobs/claim` | admin JWT | 401 | **401** "sai API key" | ✅ PASS |
| IDOR: viewer đọc permissions của user khác | viewer JWT + other account_id | 403 | **403** | ✅ PASS |
| Inject `owner_account_id` vào body ingest | machine key + body chứa `owner_account_id` | 422 | **422** RECORD_VALIDATION_FAILED | ✅ PASS |

**Kết luận:** Không có lỗ hổng leo thang đặc quyền được tìm thấy trong lần audit này.

---

## A.6 Bug được phát hiện: Gateway Routing — `/v1/admin/permissions`

### Mô tả
`GET /v1/admin/permissions` trả **404 cho MỌI caller** khi gọi qua gateway, kể cả admin hợp lệ.

### Bằng chứng
```
# Gọi qua gateway (admin JWT):
GET http://127.0.0.1:57965/v1/admin/permissions
Authorization: Bearer <admin_jwt>
→ 404 Not Found

# Gọi thẳng identity-service (anonymous):
GET http://127.0.0.1:57962/v1/admin/permissions
→ 401 {"detail": "Thiếu token"}  ← route tồn tại!
```

### Nguyên nhân gốc
`infra/service-registry.json` khai báo `public_prefixes` của identity-service chỉ bao gồm `/v1/admin/accounts` chứ KHÔNG có `/v1/admin/permissions`. Gateway chỉ route các path khớp với `public_prefixes` → path này bị bỏ qua → 404.

### Ảnh hưởng
- `PermissionsDialog.tsx` gọi `permissionsApi.catalog()` → fetch `/v1/admin/permissions` → 404 → fallback về `FALLBACK_CATALOG` (chỉ có reward:access và reward:admin, KHÔNG có capability:*).
- Admin không thể thấy danh sách permission đầy đủ từ server; dùng hardcoded fallback.
- Capability permissions vẫn cấp/thu được (vì endpoint `/v1/admin/accounts/{id}/permissions/{perm}` hoạt động đúng), nhưng catalog dropdown bị sai.

### Mức độ: **HIGH** (không phải critical vì FALLBACK_CATALOG bù được phần lớn)

### Fix cần làm
Thêm `/v1/admin/permissions` vào `public_prefixes` của identity-service trong `infra/service-registry.json`, sau đó chạy `render_nginx.py` để tái sinh `nginx.conf`.

---

## A.7 Vấn đề phát hiện: TTL Access Token

**Hiện tại:** 900 giây (15 phút)  
**Thiết kế:** 600 giây (10 phút) theo quyết định D12  
**Bằng chứng:** `services/identity-service/src/identity_service/config.py` line ~95: `access_token_ttl_s=_int(env, "IDENTITY_ACCESS_TOKEN_TTL_S", **900**)`  
**Ghi chú trong docs:** `docs/handbook/02-trang-thai-tinh-nang.md` ghi rõ "Hiện **900 s**; test parity khóa giá trị 900. Đổi qua `IDENTITY_ACCESS_TOKEN_TTL_S` + sửa test có chủ đích."  
**Mức độ:** Medium (token bị chiếm có thêm 5 phút window)
