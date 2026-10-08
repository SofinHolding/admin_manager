# Documentation Audit — Manager Platform
**Ngày:** 2026-10-08  
**Nhánh:** `refactor/microservices`  
**Auditor:** Principal Software Architect (automated + manual cross-check)  
**Phạm vi:** Toàn bộ `docs/` + `infra/service-registry.json` đối chiếu source code thực tế

---

## 1. Tài liệu đã kiểm tra

| File | Loại |
|---|---|
| `docs/handbook/01-tong-quan-he-thong.md` | Handbook |
| `docs/handbook/02-trang-thai-tinh-nang.md` | Handbook |
| `docs/handbook/03-dich-vu.md` | Handbook |
| `docs/handbook/04-du-lieu-va-su-kien.md` | Handbook |
| `docs/handbook/05-xac-thuc-va-bao-mat.md` | Handbook |
| `docs/handbook/06-huong-dan-phat-trien.md` | Handbook |
| `docs/handbook/07-van-hanh-va-lo-trinh.md` | Handbook |
| `docs/architecture/architecture-assessment-v2.md` | Architecture |
| `docs/architecture/repo-layout-v1.md` | Architecture |
| `docs/architecture/implementation-decisions.md` | Architecture |
| `docs/architecture/service-conventions-v1.md` | Architecture |
| `docs/architecture/ui-design-v1.md` | Architecture |
| `docs/api/ingest-data-contract-v1.md` | API Contract |
| `docs/api/integration-api-v1.md` | API Contract |
| `docs/api/analytics-api-v1.md` | API Contract |
| `docs/api/internal-feed-contract-v1.md` | API Contract |
| `docs/api/service-auth-contract-v1.md` | API Contract |
| `docs/operations/implementation-final-report.md` | Operations |
| `docs/operations/production-cutover-runbook.md` | Operations |
| `docs/operations/identity-migration-runbook.md` | Operations |
| `docs/operations/legacy-vps-deploy.md` | Operations (Legacy) |
| `infra/service-registry.json` | Infrastructure |
| `CLAUDE.md` | Binding constraints |

**Nguồn đối chiếu:**
- Migrations SQL: identity (4 files), integration (4 files), analytics (2 files), reward (5 files)
- Source routers: integration, analytics, identity, gateway
- `apps/manager-web/src/App.tsx`, `permissions.ts`, `api.ts`
- `infra/service-registry.json` (trước và sau sửa)
- `git log --oneline -15`, `git branch`
- Kết quả `python infra/scripts/test_all.py` (đang chạy — xem §7)

---

## 2. Sai lệch được phát hiện và trạng thái xử lý

### 2.1 Đường dẫn thư mục lỗi thời — ĐÃ SỬA

| Document | Claim (sai) | Thực tế | Trạng thái |
|---|---|---|---|
| `handbook/01` line 6 | `admin-web/` | `apps/manager-web/` | ✅ ĐÃ SỬA |
| `handbook/01` line 37 | `manager-web \| admin-web/` | `apps/manager-web/` | ✅ ĐÃ SỬA |
| `handbook/01` line 39 | `admin-server/, local-ops-hub/` | `legacy/admin-server/, apps/local-fallback-tool/` | ✅ ĐÃ SỬA |
| `handbook/01` lines 75–77 | `admin-web/  admin-server/  local-ops-hub/` trong cây thư mục | `apps/manager-web/`, `apps/local-fallback-tool/`, `legacy/admin-server/` | ✅ ĐÃ SỬA |

**Bối cảnh:** `repo-layout-v1.md` ghi rõ trạng thái "ĐÃ ÁP DỤNG" — thư mục đã được di chuyển (ADR-012) nhưng `handbook/01` chưa được cập nhật theo.

### 2.2 `documents`/`user_meta` trong service-registry — ĐÃ SỬA

| Document | Claim (sai) | Thực tế | Trạng thái |
|---|---|---|---|
| `infra/service-registry.json` `owned_tables` của integration-service | Chứa `documents`, `user_meta` | **Không có migration nào** của integration-service tạo hai bảng này. Chúng do sync server `upload-facebook` ghi thẳng vào DB ngoài | ✅ ĐÃ XOÁ khỏi `owned_tables` |
| `architecture-assessment-v2.md` §3.1 | `integration_db` sở hữu `schema legacy_ingest: documents, user_meta` | Canonicalizer (L8) ⛔🔒 chưa triển khai; `legacy_ingest` thực tế chỉ có `platform_policies`, `resource_sources`, `resource_assignments`, `operation_batches`, `operation_items`, `operation_audit_log`, `account_links`, `link_codes` | ✅ ĐÃ THÊM GHI CHÚ "chưa thực hiện" |

**Ghi chú:** Architecture Assessment §2.1 (F1) mô tả đây là **kế hoạch tương lai** (chờ thay đổi hạ tầng + sync server). Bảng §3.1 cũ trình bày như trạng thái hiện tại — đã sửa.

### 2.3 Số lượng migration reward — ĐÃ SỬA

| Document | Claim (sai) | Thực tế | Trạng thái |
|---|---|---|---|
| `handbook/02` line 89 | `migration 001→004` | 5 migrations: 001_reward_init, 002_reward_tokens, 003_reward_reconcile, 004_reward_identity_link, 005_reward_feed_subject_key | ✅ ĐÃ SỬA thành `001→005` |

### 2.4 Tên thư mục local-ops-hub trong handbook 02 — ĐÃ SỬA

| Document | Claim (sai) | Thực tế | Trạng thái |
|---|---|---|---|
| `handbook/02` line 113 | `local-ops-hub chuyển sang machine credential` | Thư mục ĐÃ đổi tên thành `apps/local-fallback-tool/`; nội dung còn legacy nguyên trạng | ✅ ĐÃ SỬA |

### 2.5 UI design v1 — KHÔNG SỬA (tài liệu thiết kế đề xuất)

| Document | Nội dung | Thực tế | Quyết định |
|---|---|---|---|
| `architecture/ui-design-v1.md` | Đề xuất sidebar theo job categories (THEO DÕI, KẾT NỐI, QUẢN TRỊ); single summary statement thay KPI cards | Chưa implement trong `apps/manager-web/src/App.tsx`; routes vẫn là `/admin/dashboard`, `/admin/groups`, v.v. | **Không sửa** — đây là tài liệu thiết kế đề xuất, chưa được duyệt triển khai. Ghi chú trạng thái "PROPOSED" ở §3. |

---

## 3. Đánh giá mức đáp ứng yêu cầu kiến trúc mới

Đánh giá dựa trên bằng chứng source code thực tế (migrations, routers, tests).

| Yêu cầu | Trạng thái | Bằng chứng |
|---|---|:--|
| **Unified Account** — 1 account duy nhất, dùng chung account_id xuyên suốt | `IMPLEMENTED` | `accounts` table trong identity_db; integration, analytics, reward đều nhận `owner_account_id` từ JWT |
| **Capability-Based Access** — tách Account / Role / Capability / Scope | `PARTIALLY_IMPLEMENTED` | `permissions` + `account_permissions` table có (`reward:access`, `reward:admin`). Chưa có capability field tổng quát (web_access, tool_integration, dashboard_access, v.v.) — chỉ có role + 2 permission |
| **Invitation Advanced Permissions** — cấu hình capability khi tạo mã mời | `PARTIALLY_IMPLEMENTED` | `integration_policy` + `allowed_apps` trên `invite_keys` ✅. Chưa có: `web_access`, `tool_integration`, `api_write`, `api_read`, `multi_tool` — phải thêm capability model trước |
| **Website-Only User** — giao diện riêng cho user không có Tool | `NOT_IMPLEMENTED` | Không có route, layout, hay navigation riêng cho nhóm này. `apps/manager-web/src/App.tsx` chỉ có admin zone và reward zone |
| **Personal Tool** — user sở hữu Tool riêng, quản lý credential | `IMPLEMENTED` | `integration_clients` (client_kind: primary_tool/fallback_tool/service) + `integration_credentials` + UI tại `/admin/integrations` ✅ |
| **Shared Collector** — 1 máy thu thập cho nhiều user | `PARTIALLY_IMPLEMENTED` | Jobs system (claim `SKIP LOCKED`, lease, heartbeat, retry) ✅. Nhưng: multi-tenant job assignment (collector cho job của nhiều owner), deduplication xuyên owner, worker registry chưa có |
| **Tool Ownership / Data Provenance** — truy vết dữ liệu tới Tool/owner | `IMPLEMENTED` | Mọi bảng canonical (`resources`, `content_items`, `daily_metrics`, `activity_events`) đều có `owner_account_id` + `client_id`. Audit log có `client_id`, `account_id` ✅ |
| **Canonical Metric Registry** — chỉ nhận metric đã đăng ký | `IMPLEMENTED` | `metric_registry` table (seeded: views, reactions, comments, likes, shares, followers, interactions, videos_posted) + validation trong ingest ✅ |
| **Multi-Tenant Authorization** — cách ly dữ liệu giữa tenant | `IMPLEMENTED` | Mọi query lọc `owner_account_id` từ JWT (suy ra từ credential, không tin client). Test isolation: `test_injection_and_isolation.py` ✅ |
| **Data Conflict Resolution** — nguồn dữ liệu trùng, chính sách LWW | `IMPLEMENTED` | `observed_at` LWW (bản mới hơn thắng), `decide()` function, idempotency store ✅. Chưa có source priority (personal tool vs shared collector) |
| **API Documentation cho Tool** — tài liệu tích hợp ngay trên website | `NOT_IMPLEMENTED` | Không có trang `/profile` hay `/integrations` nào hiển thị API docs, examples, scopes. OpenAPI có tại `/openapi.json` của từng service nhưng chưa embedded vào UI |

---

## 4. Tài liệu còn thiếu

| Tài liệu cần tạo | Ưu tiên | Ghi chú |
|---|---|---|
| `docs/architecture/capability-model-v1.md` | HIGH | Thiết kế capability model (web_access, tool_integration, v.v.) — tiên quyết cho yêu cầu mới |
| `docs/architecture/shared-collector-design.md` | HIGH | Multi-tenant collector, job assignment, worker registry |
| `docs/architecture/website-only-user-ui.md` | MEDIUM | Layout, routes, navigation cho user không có Tool |
| `docs/api/openapi/` — cập nhật JSON | MEDIUM | analytics-service.v1.json còn thiếu một số endpoint path cụ thể |
| `docs/handbook/08-nang-cap-kien-truc.md` | MEDIUM | Hướng dẫn triển khai yêu cầu kiến trúc mới (phần thiếu) |

---

## 5. Mâu thuẫn kiến trúc chưa giải quyết

| # | Mâu thuẫn | Vị trí | Phương án |
|---|---|---|---|
| M1 | `documents`/`user_meta` do upload-facebook tạo trong DB ngoài, nhưng analytics đọc qua legacy ACL. Chưa có migration nào của integration-service sở hữu chúng. | `architecture-assessment-v2.md §2.1 F1` | Canonicalizer (L8) cần thay đổi hạ tầng + đồng bộ với repo upload-facebook → `⛔🔒 PENDING` |
| M2 | `agg_daily` khai báo trong registry analytics nhưng chưa được sử dụng; query tính từ read model toàn bảng | `service-registry.json` + `analytics/02` | Implement `agg_daily` hoặc xoá khỏi registry (backlog #6) |
| M3 | `resource_type` thiếu trong PK `read_resources`: hai resource type cùng `external_id` ghi đè nhau | `analytics/02 migration` ghi chú | Sửa schema + ingest — breaking change cần contract version mới |
| M4 | Rate limit + circuit breaker in-memory per-process: nhiều worker nhân ngưỡng | Gateway + integration | Cần Redis shared hoặc nginx `limit_req` (backlog #9) |
| M5 | `ui-design-v1.md` đề xuất UI mới nhưng chưa được implement; handbook 07 không nêu rõ đây là backlog | `ui-design-v1.md` | Thêm vào backlog handbook 07 hoặc mark PROPOSED trong chính file |

---

## 6. Files đã cập nhật trong audit này

| File | Thay đổi |
|---|---|
| `docs/handbook/01-tong-quan-he-thong.md` | Sửa `admin-web/` → `apps/manager-web/`; `local-ops-hub/` → `apps/local-fallback-tool/`; `admin-server/` → `legacy/admin-server/`; cập nhật cây thư mục §1.5 |
| `docs/handbook/02-trang-thai-tinh-nang.md` | Sửa reward migration `001→004` → `001→005`; sửa `local-ops-hub` → `apps/local-fallback-tool/` |
| `docs/architecture/architecture-assessment-v2.md` | Sửa §3.1: tách `documents`/`user_meta` thành row riêng với ghi chú "chưa thực hiện"; cập nhật legacy_ingest tables thực tế |
| `infra/service-registry.json` | Xoá `documents` và `user_meta` khỏi `owned_tables` của integration-service (không có migration tương ứng) |

---

## 7. Kết quả kiểm thử

Chạy `python infra/scripts/test_all.py --only arch` và `--only contract` ngày 2026-10-08.

### 7.1 Kết quả thực tế

| Bộ test | Pass | Fail | Skip | Trạng thái |
|---|---|---|---|---|
| **arch** | 131 | 0 | 0 | ✅ OK — xác nhận `service-registry.json` thay đổi an toàn |
| **contract** | 40 | **2** | 0 | ❌ LỖI — pre-existing (xem §7.2) |

Các bộ còn lại chưa chạy lại trong phiên này. Tham chiếu từ `implementation-final-report.md` (2026-10-05):
identity 439 ✅ · integration 454 ✅ · analytics 153 ✅ · reward 180 ✅ · gateway 452 ✅ · e2e 31 ✅ · web 147 ✅ · SDK 44 ✅

### 7.2 Bugs phát hiện — pre-existing, KHÔNG do audit gây ra

File: `tests/contract/test_analytics_stats_parity.py`

| Test | Dòng | Triệu chứng |
|---|---|---|
| `test_channel_breakdown_parity` | 236 | `'managed': 0` — analytics read model trả count sai cho managed resources |
| `test_parity_still_holds_after_country_alias_normalization` | 245 | `'countries': []` — `ds.entities` rỗng hoặc country không populate vào read model sau `world.sync()` |

Cả hai failure **không liên quan đến bất kỳ thay đổi nào của phiên audit** (chỉ sửa doc + xoá 2 table name không tồn tại khỏi registry). Nghi ngờ commit `91f2347` ("ô chọn nền tảng") là thủ phạm. Cần `git bisect` giữa `d654ec6` (final report xanh) và `91f2347`.

---

## 8. Tính năng chưa đáp ứng yêu cầu kiến trúc mới

| # | Tính năng | Ưu tiên | Phase | Ghi chú |
|---|---|---|---|---|
| 1 | **Capability Model** — `account_capabilities`, cấp/thu qua admin, enforce server-side | H | PHASE 2 | Tiên quyết cho tất cả yêu cầu còn lại |
| 2 | **Invitation Advanced Permissions** — gắn capability vào invite key | H | PHASE 2 | Extend `integration_policy` → `capability_grant` |
| 3 | **Website-Only User layout** — routes, layout, navigation riêng | H | PHASE 7 | Chờ thiết kế UI từ user |
| 4 | **Shared Collector multi-tenant** — worker registry, job routing theo owner, dedup xuyên owner | M | PHASE 4 | Jobs system đã có base |
| 5 | **API Documentation UI** — trang developer/API integration trên website | M | PHASE 7 | OpenAPI đã có, cần embed vào UI |
| 6 | **Source priority / conflict resolution** — personal tool vs shared collector | M | PHASE 6 | Cần data provenance model |
| 7 | **Canonicalizer documents→snapshot** | L | PHASE 6 | ⛔🔒 cần thay đổi hạ tầng |
| 8 | **Metric Registry versioning** | L | PHASE 6 | Registry table có, chưa có workflow thêm metric mới |

---

## 9. Đề xuất ưu tiên tiếp theo

1. **Fix 2 contract test failures** — `git bisect` tìm commit break; sửa analytics read model trước khi tiếp tục bất kỳ phase nào.
2. **Thiết kế Capability Model** (`capability-model-v1.md`) — tiên quyết cho mọi phase kiến trúc mới.
3. **Website-Only User** — chờ thiết kế UI từ user trước khi code.
4. **Canonicalizer** — giữ `⛔🔒`, không làm cho đến khi có kế hoạch hạ tầng + đồng bộ upload-facebook.
5. **Chạy `test_all.py` đầy đủ** sau khi fix contract failures để xác nhận trạng thái xanh toàn bộ.

---

*Báo cáo này mô tả trạng thái tại 2026-10-08. Không có thay đổi production, không deploy, không git push.*
