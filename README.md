# Manager Platform

Nền tảng quản lý/giám sát nội dung đa nền tảng theo kiến trúc **microservices** (tái cấu trúc từ hệ Admin Manager cũ theo Strangler Pattern).
Người dùng xem Dashboard và quản trị qua web; các Tool ngoài đẩy dữ liệu vào bằng API tích hợp thống nhất (machine credential).

> **Bắt đầu ở đây:** [`docs/handbook/README.md`](docs/handbook/README.md) (tổng quan, trạng thái đã/chưa làm, mô tả từng service, hướng dẫn phát triển, backlog).
> Luật ràng buộc cho mọi thay đổi: [`CLAUDE.md`](CLAUDE.md). Chuẩn cấu trúc thư mục: [`docs/architecture/repo-layout-v1.md`](docs/architecture/repo-layout-v1.md).

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| [`services/`](services/) | Backend độc lập, cùng một skeleton: `api-gateway`, `identity-service`, `integration-service`, `analytics-service`, `reward-service` |
| [`apps/`](apps/) | Ứng dụng người dùng/biên: `manager-web` (React/Vite), `local-fallback-tool` (Tool B) |
| [`packages/`](packages/) | Mã dùng chung (chỉ generic): `contracts`, `observability`, `sdk-python` |
| [`infra/`](infra/) | `service-registry.json` (nguồn sự thật), `gateway/`, `docker/` (hạ tầng DEV), `compose/` (stack), `deploy/`, `scripts/` |
| [`tests/`](tests/) | Test xuyên service: `arch`, `contract`, `integration`, `e2e` |
| [`docs/`](docs/) | `handbook/`, `architecture/`, `api/`, `operations/` |
| [`legacy/`](legacy/) | Hệ cũ đóng băng (`admin-server`) — chỉ sửa lỗi nghiêm trọng |

## Chạy nhanh

```bash
python infra/scripts/bootstrap_dev.py                                          # .env.dev ngẫu nhiên (không commit)
docker compose --env-file .env.dev -f infra/docker/compose.dev.yml up -d       # Postgres 55440 + Redis 26390 riêng của dự án
python infra/scripts/test_all.py                                               # cổng chất lượng đầy đủ
python infra/scripts/dev_stack.py up                                           # chạy cả stack bằng tiến trình thật
```

Chỉ commit cục bộ; **không push/deploy** khi chưa có chỉ thị rõ ràng của chủ dự án (xem CLAUDE.md §7). Production: `docs/operations/production-cutover-runbook.md`.
