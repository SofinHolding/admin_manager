# Capability Model v1 — Manager Platform

**Trạng thái:** ĐÃ TRIỂN KHAI (Phase 2) — xem `docs/architecture/implementation-decisions.md` ADR-013.
**Ngày:** 2026-10-08  
**Thay thế:** Không — bổ sung cho permission system hiện có.

---

## 1. Vấn đề

Role hiện tại (`viewer`, `admin`, `dash_admin`, `discord`) gộp hai khái niệm:
- **Vai trò website** (ai được làm gì trên dashboard)
- **Tính năng tài khoản** (có được dùng Tool không, có được nhập kênh không)

Kết quả: không thể có một `viewer` được phép dùng Tool mà không đổi role của họ; không thể có một `viewer` chỉ xem website mà không có Tool.

---

## 2. Giải pháp: Capability = Named Permission

**Tái dùng `permissions` + `account_permissions` hiện có.** Không thêm bảng mới.

Capability chỉ là permission với namespace `capability:*`:

```
capability:web_access            — đăng nhập, xem website cơ bản
capability:dashboard_access      — xem Dashboard thống kê
capability:manual_channel_mgmt   — tự nhập/xóa kênh trên website
capability:tool_integration      — liên kết Tool, tạo integration_client
capability:api_write             — credential có scope snapshots:write/metrics:write
capability:api_read              — credential có scope read
capability:multi_tool            — nhiều hơn 1 integration_client
```

### Tại sao không tạo bảng riêng?

`permissions` + `account_permissions` đã có:
- Tên permission (PK)
- `admin_only` flag (ngăn user tự cấp)
- `granted_by` (audit trail)
- `granted_at`
- FK to accounts

Tất cả đủ cho capability. Thêm bảng thứ 3 là duplicate.

---

## 3. Mapping Role → Default Capabilities

Khi đăng ký (hoặc migration một lần), tài khoản nhận capabilities mặc định theo role:

| Role | web_access | dashboard_access | manual_channel_mgmt | tool_integration | api_write | api_read | multi_tool |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `viewer` | ✅ | ✅ | — | — | — | — | — |
| `dash_admin` | ✅ | ✅ | — | — | — | — | — |
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `discord` | — | — | — | — | — | — | — |

Sau khi có default, admin có thể cấp thêm/thu hồi từng capability độc lập.

---

## 4. Invite Key Extension: `capability_grant`

Thêm cột `capability_grant JSONB` vào `invite_keys`. Khi đăng ký:

```json
{
  "capabilities": ["capability:web_access", "capability:dashboard_access", "capability:tool_integration"],
  "override_defaults": false
}
```

- `override_defaults: false` → cấp thêm vào default của role
- `override_defaults: true` → chỉ cấp đúng những gì trong list (dùng cho discord/special)

---

## 5. JWT Claims

Access token đã có `perms: ["reward:access"]`. Capability sẽ tự xuất hiện theo cùng cơ chế:

```json
{
  "sub": "account_id",
  "role": "viewer",
  "perms": [
    "capability:web_access",
    "capability:dashboard_access",
    "capability:tool_integration",
    "reward:access"
  ]
}
```

Frontend và service đọc `perms` — không cần thay đổi JWT structure.

---

## 6. Authorization Flow

```
Request → Gateway → Service
                      │
                      ├─ Verify JWT (offline, JWKS)
                      ├─ Check role (coarse gate)
                      └─ Check perms (fine gate)
                             │
                             └─ "capability:tool_integration" ∈ perms?
                                   YES → allow create integration_client
                                   NO  → 403 CAPABILITY_REQUIRED
```

Backend tự kiểm tra từ JWT. Frontend chỉ dùng để ẩn/hiện UI — **không dùng để cấp quyền**.

---

## 7. Admin API mới

```
POST   /v1/admin/accounts/{id}/capabilities        Cấp capability
DELETE /v1/admin/accounts/{id}/capabilities/{cap}  Thu hồi
GET    /v1/admin/accounts/{id}/capabilities        Danh sách hiện tại
```

Chỉ `admin` được gọi. Server kiểm tra người gọi không tự cấp `admin_only` perms cho bản thân.

---

## 8. UI Routing dựa trên Capability

```
/login              → mọi người
/register           → mọi người

/                   → redirect → homePath(user)

homePath(user):
  canSeeDashboard   → /admin/dashboard
  tool_integration  → /my-tools (nếu KHÔNG có dashboard)
  manual_channel    → /channels (website-only)
  web_access only   → /profile

/admin/*            → role: viewer/dash_admin/admin
/my-tools           → capability:tool_integration
/channels           → capability:manual_channel_mgmt
/reward/*           → perm: reward:access
/profile            → mọi authenticated
```

---

## 9. Thứ tự triển khai (PHASE 2)

1. Migration `0005_capabilities.sql` — seed perms, thêm `capability_grant` vào invite_keys
2. Sửa registration flow — grant default capabilities + capability_grant từ invite key
3. Sửa JWT build — đã tự động (perms được query từ account_permissions)
4. Admin API — 3 endpoints mới trong identity-service
5. Frontend — permissions.ts mới, routing mới, UI invitation checkbox

---

## 10. Tương thích ngược

- Tài khoản cũ: migration seed capabilities mặc định theo role hiện tại → **không mất quyền cũ**
- Invite key cũ: `capability_grant = null` → dùng default mapping → **không break**
- JWT cũ: thêm `capability:*` vào `perms` → client cũ chỉ thấy thêm, không mất gì → **không break**
