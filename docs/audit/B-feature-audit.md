# B. Feature Audit Report

**Phương pháp:** Source code analysis + live API testing + test execution  
**Chú thích kết quả:** PASS | PARTIAL | FAIL | MISSING | CONFLICT | NOT_TESTED

---

## B.1 Bảng tổng hợp tính năng

| Feature | Tài liệu | Impl | Frontend | Backend | Permission | Test | Kết quả |
|---|:-:|:-:|:-:|:-:|:-:|:-:|---|
| **IDENTITY SERVICE** | | | | | | | |
| Đăng ký / login / logout / refresh | ✅ | ✅ | ✅ | ✅ | Public | 439 test (partial run) | PASS |
| Invite key (role, max_uses, expires) | ✅ | ✅ | ✅ | ✅ | admin | ✅ | PASS |
| Invite key + integration_policy | ✅ | ✅ | ✅ | ✅ | admin | ✅ | PASS |
| Invite key + capability_grant | ✅ | ✅ | ✅ | ✅ | admin | ✅ | PASS |
| Capability model (7 caps) | ✅ | ✅ | ✅ | ✅ | admin grant/revoke | migration 0005 | PASS |
| JWT RS256 + JWKS xoay | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| Phiên `sid` thu hồi | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| Permission catalog API `/v1/admin/permissions` | ✅ | ✅ | ✅ | ✅ | admin | **404 via gateway** 🐛 | FAIL |
| Cấp/thu permission (`PUT/DELETE /v1/admin/accounts/{id}/permissions/{perm}`) | ✅ | ✅ | ✅ | ✅ | admin | ✅ live | PASS |
| OIDC provider (PKCE, consent, userinfo) | ✅ | ✅ | ✅ | ✅ | — | unit only | NOT_TESTED (browser) |
| Email xác thực (SMTP thật) | ✅ | 🟡 | — | 🟡 LogMailer | — | — | PARTIAL (dev=log) |
| TTL access token 600s | ✅ | ❌ | — | ❌ (900s) | — | parity@900s | FAIL |
| Introspection cho thao tác nhạy cảm | ✅ | 🟡 | — | 🟡 endpoint có, chưa dùng | — | — | PARTIAL |
| **INTEGRATION SERVICE** | | | | | | | |
| Client (create/list/patch) | ✅ | ✅ | ✅ | ✅ | tool_integration | ✅ | PASS |
| Credential (issue/rotate/revoke) | ✅ | ✅ | ✅ | ✅ | owner/admin | ✅ | PASS |
| Snapshot ingest (full/delta/commit) | ✅ | ✅ | — | ✅ | machine scope | ✅ | PASS |
| Resource batch upsert | ✅ | ✅ | — | ✅ | machine scope | ✅ | PASS |
| Jobs queue (claim/heartbeat/complete) | ✅ | ✅ | ✅ | ✅ | machine jobs:claim | ✅ | PASS |
| Legacy ACL (`/v1/resources`, `/v1/ops`) | ✅ | ✅ | ✅ | ✅ | user JWT | ✅ | PASS |
| Channel groups (viewer tự quản) | ✅ | ✅ | ✅ | ✅ | viewer+ | ✅ | PASS |
| `GET /v1/integrations/resources` (machine, mới) | ⚠️ undoc | ✅ | ✅ | ✅ | jobs:claim | **Không có test** | NOT_TESTED |
| `GET /v1/integrations/resource-owners` (machine, mới) | ⚠️ undoc | ✅ | ✅ | ✅ | jobs:claim | **Không có test** | NOT_TESTED |
| fallback_tool sees all owners (permission_changed handler) | ⚠️ undoc | ✅ | — | ✅ | — | **Không có test** | NOT_TESTED |
| Rate limit user plane (integration) | ✅ (⛔ cũ) | 🟡 | — | 🟡 (memory only) | — | — | PARTIAL |
| Canonicalizer documents→snapshot | ✅ (⛔🔒) | ❌ | — | ❌ | — | — | MISSING |
| `require_tool_integration` → 403 nếu thiếu capability | ✅ | ✅ | — | ✅ | — | **Không có test 403** | PARTIAL |
| **ANALYTICS SERVICE** | | | | | | | |
| Consumer canonical (revision-based) | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| Summary / traffic / top-channels | ✅ | ✅ | ✅ | ✅ | viewer+ | ✅ | PASS |
| FilterContext (owner/platform/country/project/group/date) | ✅ | ✅ | ✅ | ✅ | — | ✅ | PASS |
| Cache-Aside Redis | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| `agg_daily` bảng tổng hợp | ✅ (⛔) | ❌ | — | ❌ | — | — | MISSING |
| PK `read_resources` gồm resource_type | ✅ (⛔) | ❌ | — | ❌ | — | — | MISSING (schema gap) |
| **REWARD SERVICE** | | | | | | | |
| Job slash-command engine | ✅ | ✅ | ✅ | ✅ | reward:access | ✅ | PASS |
| RS256 verify + reward:access/reward:admin | ✅ | ✅ | ✅ | ✅ | — | ✅ | PASS |
| Consumer feed Identity → denylist | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| `deploy-reward.yml` mô hình mới | ✅ (⛔🔒) | ❌ | — | ❌ | — | — | MISSING (deploy only) |
| **GATEWAY** | | | | | | | |
| CORS + security headers + rate limit | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| Chặn `/internal` | ✅ | ✅ | — | ✅ | — | ✅ live | PASS |
| Strip X-Service-Key | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| Circuit breaker | ✅ | ✅ | — | ✅ | — | ✅ | PASS |
| WebSocket | ✅ (⛔) | ❌ | — | ❌ | — | — | MISSING (no need) |
| `nginx -t` thật | ✅ | 🔒 | — | — | — | — | NOT_TESTED |
| **MANAGER WEB** | | | | | | | |
| Admin: invite key + integration_policy | ✅ | ✅ | ✅ | — | admin | ✅ | PASS |
| Admin: Tài khoản (lock/edit/delete) | ✅ | ✅ | ✅ | ✅ | admin | ✅ | PASS |
| Admin: PermissionsDialog (capability + reward) | ✅ | ✅ | ✅ | ✅ (catalog 404🐛) | admin | partial | PARTIAL |
| Admin: Công cụ kết nối (tạo client, cấp key) | ✅ | ✅ | ✅ | ✅ | admin | — | PASS |
| Admin: Scope editing trên client | ⚠️ new | ✅ | ✅ | ✅ | admin | — | NOT_TESTED |
| Dashboard thống kê | ✅ | ✅ | ✅ | ✅ | viewer+ | ✅ | PASS |
| FilterContext đồng bộ URL | ✅ | ✅ | ✅ | — | — | ✅ | PASS |
| Refresh token KHÔNG vào localStorage | ✅ | ✅ | ✅ | — | — | ✅ | PASS |
| OIDC login mode | ✅ | ✅ | ✅ | — | — | mock only | NOT_TESTED (browser) |
| Thử trên browser thật với stack thật | ⚠️ (⛔) | ❌ | — | — | — | — | NOT_TESTED |
| Bundle scan (không có secret) | ✅ | ✅ | — | — | — | ✅ | PASS |
| **LOCAL FALLBACK TOOL** | | | | | | | |
| Collector thu thập dữ liệu kênh | ✅ | ✅ | ✅ | ✅ (Electron) | machine | — | PASS |
| ChannelImportPage (users + channels local DB) | ⚠️ new | ✅ | ✅ | ✅ | machine | — | NOT_TESTED |
| `channels_db.py` SQLite local | ⚠️ new | ✅ | — | ✅ | — | smoke only | PARTIAL |
| Auth separation machine vs user | ✅ | ✅ | — | ✅ | — | live | PASS |

---

## B.2 Các vấn đề PARTIAL / FAIL / NOT_TESTED cần chú ý

### FAIL (cần sửa ngay)
1. **`GET /v1/admin/permissions` → 404 qua gateway** — Bug gateway routing (xem A.6)
2. **TTL access token 900s** — Chưa sửa về 600s theo D12

### NOT_TESTED (cần thêm test)
1. `GET /v1/integrations/resources` — Endpoint mới, 0 test
2. `GET /v1/integrations/resource-owners` — Endpoint mới, 0 test  
3. `_permission_changed` handler auto-provision client — 0 test
4. `require_tool_integration` → 403 khi viewer thiếu capability — 0 test
5. OIDC với browser thật — Test chỉ dùng mock/jsdom

### PARTIAL (hoạt động nhưng chưa đầy đủ)
1. **Email SMTP** — Chỉ có LogMailer trong DEV (in ra log)
2. **Introspection** — RFC 7662 có, nhưng không service nào dùng cho thao tác nhạy cảm
3. **Rate limit integration user** — In-memory per-process (nhân theo số worker)
4. **`require_tool_integration` 403** — Logic đúng nhưng thiếu test regression
5. **PermissionsDialog catalog** — Fallback hoạt động nhưng catalog server bị 404

### MISSING (chưa làm, có trong roadmap)
1. Canonicalizer `documents→snapshot` (⛔🔒 Phase 6)
2. `agg_daily` analytics aggregation (⛔ backlog)  
3. PK `read_resources` gồm `resource_type` (⛔ schema gap)
4. `deploy-reward.yml` mô hình mới (⛔🔒)
5. WebSocket support
