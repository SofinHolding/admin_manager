# E. Missing Features Report

---

## Critical

| # | Tính năng | Mô tả | Nơi cần thêm |
|---|---|---|---|
| E1 | **Test cho endpoint machine mới** | `GET /v1/integrations/resources` và `resource-owners`: 0 test hiện có. Rủi ro data leak giữa owners. | `services/integration-service/tests/` |
| E2 | **Test cho `_permission_changed` handler** | Auto-provision client khi cấp `capability:tool_integration` — không có test. | `services/integration-service/tests/test_provisioning.py` |
| E3 | **Test `require_tool_integration` → 403** | Thiếu regression test cho case viewer thiếu capability. | `services/integration-service/tests/test_clients_credentials.py` |

## High

| # | Tính năng | Mô tả | Nơi cần thêm |
|---|---|---|---|
| H1 | **CORS từ env** | `IDENTITY_CORS_ORIGINS` thay hardcode localhost | `services/identity-service/src/identity_service/main.py` |
| H2 | **E2E cô lập DB** | Test suite không chạy được tin cậy khi dev stack đang chạy | `infra/scripts/test_all.py` + CI config |
| H3 | **Gateway prefix `/v1/admin/permissions`** | Xem D.1 | `infra/service-registry.json` |
| H4 | **Test browser thật cho OIDC** | Toàn bộ OIDC chỉ test bằng jsdom + mock | E2E với Playwright/Cypress |

## Medium

| # | Tính năng | Mô tả | Phase |
|---|---|---|---|
| M1 | **TTL access token** | Sửa 900s → 600s theo D12 | Config + test |
| M2 | **`read_resources` PK + resource_type** | Schema gap gây ghi đè analytics data | Phase 6, breaking |
| M3 | **Introspection cho thao tác nhạy cảm** | Endpoint RFC 7662 có, chưa ai dùng (D13) | Đang xem xét |
| M4 | **`agg_daily` analytics** | Khai báo trong registry nhưng chưa implement | Phase backlog |
| M5 | **Shared collector multi-tenant** | Worker registry, job routing theo owner | Phase 4 (partial) |
| M6 | **API doc UI trên website** | OpenAPI có, chưa embed vào `/my-tools` | Phase 7 |
| M7 | **Website-only user layout** | Routes, nav, layout riêng cho user không có Tool | Phase 7 (cần UI design) |

## Low

| # | Tính năng | Mô tả | Phase |
|---|---|---|---|
| L1 | **SDK TypeScript** | `packages/sdk-typescript` chưa có | Backlog |
| L2 | **Canonicalizer documents→snapshot** | Cần thay đổi hạ tầng | Phase 6, ⛔🔒 |
| L3 | **Push via Redis Streams** | DLQ + replay đã có; push chưa cần | Phase 5 |
| L4 | **`deploy-reward.yml` mô hình mới** | Hiện theo mô hình cũ | Production, ⛔🔒 |
| L5 | **WebSocket** | Blocked, chưa cần | N/A |
| L6 | **CI/CD pipeline đầy đủ** | `ci-tests.yml` chỉ `workflow_dispatch`, chưa auto | Phase 6 |
| L7 | **Nginx -t với binary thật** | `render_nginx.py` xanh, nhưng validate thật chưa chạy | ⛔🔒 |

---

## Các điểm "đã ghi trong tài liệu là sẽ làm" nhưng vẫn chưa đổi

| ADR / Quyết định | Trạng thái trong tài liệu | Thực tế |
|---|---|---|
| D12: TTL 600s | "Đổi qua env + sửa test" | Chưa đổi (900s) |
| D13: Introspection fail-closed | "Endpoint có; chưa service nào dùng" | Vẫn chưa dùng |
| Phase 1.3: Identity cutover | "Chưa chạy trên dữ liệu thật" 🔒 | Chờ duyệt production |
| Phase 6: Retire legacy | 🔒 | Chưa làm |
| CI/CD auto | "ci-tests.yml chỉ workflow_dispatch" | Chưa sửa |
