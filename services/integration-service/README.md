# integration-service

Cổng duy nhất cho Tool/hệ thống ngoài: client, credential, resource, snapshot, ingest, job, audit.

| | |
|---|---|
| Cổng | 8431 |
| Database (riêng) | `integration_db` |
| Package | `integration_service` |
| Trạng thái | scaffold (chưa có nghiệp vụ; module được hiện thực theo phase) |

## Module

| Module | Phase | Trách nhiệm |
|---|---|---|
| `clients` | 2.1 | Integration Client và liên kết với account |
| `credentials` | 2.1 | cấp, xoay, thu hồi API key (lưu hash), scope |
| `installations` | 2.1 | installation/thiết bị và handshake |
| `platforms` | 2.2 | Platform Registry và Metric Registry (dữ liệu, không phải code) |
| `projects` | 2.2 | project/source namespace, tự upsert |
| `resources` | 2.2 | resource (page/channel/profile), liên kết project, execution_mode |
| `idempotency` | 2.2 | kho Idempotency-Key và phát lại response |
| `snapshots` | 2.3 | state machine snapshot, commit atomic, current pointer |
| `ingest` | 2.3 | validate và canonicalize resources/content/metrics/events |
| `feed` | 2.3 | outbox và change feed (pull) cho snapshot.committed |
| `legacy_acl` | 2.4 | Anti-Corruption Layer: dịch documents cũ sang canonical, pairing tương thích |
| `jobs` | 2.5 | hàng đợi job cho Fallback Tool, claim/complete/fail với lease |
| `provisioning` | 2.6 | tạo client từ event account.registered, báo kết quả |
| `audit` | 2.2 | audit client/installation/request |

## Dữ liệu và sự kiện (theo `infra/service-registry.json`)

- Bảng sở hữu: `integration_clients`, `integration_credentials`, `installations`, `platform_registry`, `metric_registry`, `projects`, `resources`, `resource_project_bindings`, `snapshots`, `snapshot_items`, `content_items`, `daily_metrics`, `activity_events`, `idempotency_keys`, `jobs`, `audit_log`, `account_projection`, `outbox`, `documents`, `user_meta`, `account_links`, `link_codes`
- Phát event: `snapshot.committed`, `resource.changed`, `integration.client.provisioned`, `integration.client.provision_failed`
- Nhận event: `account.registered`, `account.verified`, `account.status_changed`, `account.deleted`

## Chạy

```bash
cd services/integration-service
python -m pytest -q                  # test riêng của service
python -m uvicorn integration_service.main:app --port 8431
```

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
