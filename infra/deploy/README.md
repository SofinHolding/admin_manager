# infra/deploy — triển khai theo môi trường

Thư mục dành cho manifest/script triển khai. **Mọi thứ production là `PENDING_PRODUCTION_APPROVAL`** — không tự chạy, không đẩy lên VPS khi chưa có chỉ thị rõ ràng của chủ dự án.

| Đường dẫn | Nội dung |
|---|---|
| `legacy/setup-vps.sh` | Thiết lập thủ công VPS cho hệ cũ (admin-server + web). Tài liệu: `docs/operations/legacy-vps-deploy.md` |
| (tương lai) `<môi trường>/` | Manifest cho staging/production (compose/systemd/k8s), mỗi service build/deploy độc lập từ `services/<x>/Dockerfile` |

Chạy thử toàn nền tảng bằng container ở máy dev: `infra/compose/stack.yml` (cần hạ tầng dữ liệu DEV). Runbook chuyển production: `docs/operations/production-cutover-runbook.md`.
