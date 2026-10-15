# C. Integration Audit Report

**Phương pháp:** Source code analysis + live event verification + contract tests

---

## C.1 Luồng tích hợp service-to-service

### C.1.1 Identity → Integration (Provisioning Consumer)

```
Identity phát event → /internal/v1/feed → Integration consumer kéo
```

| Event | Handler | Status | Test |
|---|---|---|---|
| `account.registered` + provisioning | `_registered()` tạo client `pending_email_verification` | ✅ | ✅ |
| `account.verified` | `_verified()` → client `active` | ✅ | ✅ |
| `account.status_changed` | `_status_changed()` → suspend/restore client + thu hồi credential | ✅ | ✅ |
| `account.deleted` | `_deleted()` → xóa client | ✅ | ✅ |
| `account.permission_changed` | `_permission_changed()` → tạo primary_tool nếu chưa có + grant `capability:tool_integration` | ✅ (mới) | **❌ 0 test** |

**Vấn đề:** `account.permission_changed` chưa có test tự động. Nếu handler có bug, sẽ không được phát hiện.

---

### C.1.2 Integration → Analytics (Feed Consumer)

```
Integration phát snapshot.committed/resource.changed → /internal/v1/feed → Analytics consumer kéo
```

| Luồng | Status | Bằng chứng |
|---|---|---|
| Consumer kéo canonical theo revision | ✅ | `tests/contract/test_integration_analytics_reconcile.py` |
| Checksum reconcile theo đúng công thức | ✅ | `sha256(sorted_lines)` đúng thuật toán |
| Isolation: dữ liệu theo owner_account_id | ✅ | `tests/integration/test_injection_and_isolation.py` |
| `agg_daily` tổng hợp | ❌ | Chưa implement (query tính từ read model) |

**Vấn đề M3 (từ audit 2026-10-08, vẫn tồn tại):** PK `read_resources` chưa gồm `resource_type` → hai resource type cùng `external_id` trong cùng owner+platform sẽ ghi đè nhau. Breaking change cần contract version mới.

---

### C.1.3 Identity → Reward (Feed Consumer)

```
Identity phát account.* → /internal/v1/feed → Reward consumer kéo
```

| Event xử lý | Status | Test |
|---|---|---|
| `account.registered` → account_projection | ✅ | ✅ |
| `account.status_changed=suspended` → dừng job | ✅ | ✅ |
| `account.permission_changed` → không xử lý | — | — |
| `session.revoked` → dừng mọi job của account | ✅ | ✅ |

---

### C.1.4 Integration → Identity (Kết quả provisioning)

```
Integration phát integration.client.provisioned/provision_failed → Identity consumer cập nhật integration_provisioning
```

| Luồng | Status | Bằng chứng |
|---|---|---|
| Provisioning thành công → `integration_provisioning.status=active` | ✅ | `services/identity-service/src/identity_service/feed_consumer.py` |
| Provisioning thất bại → dead-letter + notify Identity | ✅ | handler `_registered` + `dead_letters` |

---

## C.2 Mặt phẳng Machine vs User — Tách biệt API

| Test | Kết quả |
|---|---|
| JWT user → `/v1/integrations/jobs/claim` (machine) | **401** "sai API key" ✅ |
| API key → `/v1/ops/users` (user JWT needed) | **403** ✅ |
| API key → `/v1/analytics/v2/summary` (JWT needed) | **401** ✅ |
| `/internal/v1/feed` qua gateway | **404** (route không tồn tại ở gateway level) ✅ |
| X-Service-Key header đến gateway | Bị strip ở 17 upstream configs ✅ |

---

## C.3 Ownership / Data Provenance

| Quy tắc | Status | Bằng chứng |
|---|---|---|
| Owner suy ra từ credential (không tin body) | ✅ | `owner*` ở body → 422 |
| Isolation theo owner_account_id | ✅ | Test injection + isolation |
| Tool đẩy resource chiếm luôn nếu trùng key | ✅ | `origin='tool'` takes over |
| Resource nhập tay không bị snapshot full Tool xóa | ✅ | `origin <> 'tool'` filter |
| Fallback_tool thấy tất cả owners (mới) | ✅ code | **❌ 0 test** |

---

## C.4 Idempotency

| Tính năng | Status | Note |
|---|---|---|
| `Idempotency-Key` header ở tất cả endpoint | ✅ | Lưu response thành công, phát lại đúng byte |
| Dead-letter + replay provisioning | ✅ | `python -m identity_service.feed_consumer replay` |
| Snapshot superseded (full cũ hơn committed) | ✅ | Warning `SNAPSHOT_SUPERSEDED` |
| Provisioning idempotent khi replay | ✅ | `account.registered` fill-only khi replay |
| `account.permission_changed` idempotent | ✅ code | check `by_owner` trước khi tạo; **0 test** |

---

## C.5 Vấn đề phát hiện trong Integration Flows

### C.5.1 E2E Test Fixture Timeout
`tests/e2e` có 16 errors đều từ fixture `world` timeout 30s chờ Integration nhận `account.verified`. Nguyên nhân: dev stack đang chạy song song tranh chấp Postgres lock với test suite.

**Ảnh hưởng:** Không có E2E evidence đáng tin. Cần môi trường DB cô lập khi chạy test.

### C.5.2 E2E Test Violation — Hardcoded Platform Name
`tests/e2e/DashboardPage.test.tsx` dùng chuỗi `"toolx_social"` — vi phạm rule "không hardcode tên Tool" trong project conventions.

**Ảnh hưởng:** 1 test fail, CI sẽ block nếu rule được enforce.

### C.5.3 Contract Tests Flaky do DB Deadlock
`tests/contract` khi chạy cùng dev stack bị TRUNCATE deadlock. Cần một trong hai: dừng dev stack trước khi chạy test, hoặc dùng DB schema riêng cho test.

---

## C.6 Đánh giá tổng thể Integration Flows

| Luồng | Trạng thái |
|---|---|
| Identity → Integration provisioning | ✅ PASS (có test, xanh khi cô lập) |
| Identity → Reward feed | ✅ PASS |
| Integration → Analytics canonical | ✅ PASS (contract test xanh) |
| Integration → Identity kết quả provisioning | ✅ PASS |
| Machine auth tách User auth | ✅ PASS (live test) |
| Ownership isolation | ✅ PASS (test + live) |
| Idempotency | ✅ PASS (core; mới chưa test) |
| `account.permission_changed` flow | ❌ NOT_TESTED |
| E2E end-to-end | ❌ FAIL (fixture timeout + hardcode) |
