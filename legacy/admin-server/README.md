# legacy/admin-server — ĐÓNG BĂNG

Hệ Admin Manager cũ (FastAPI) đang chạy production (pm2 `ufadmin`, `127.0.0.1:8421`). **Không thêm tính năng ở đây** — chỉ sửa lỗi nghiêm trọng.
Chức năng đã/đang được thay bằng: `services/identity-service` (auth, tài khoản, invite key), `services/integration-service` (resources/ops/viewer-links),
`services/analytics-service` (Dashboard), `services/reward-service`. Route nào đi service nào: `infra/gateway/route-map.json`.

- Test: `cd legacy/admin-server && python -m pytest -q` (`test_all.py --legacy`).
- Deploy cũ (chỉ workflow thủ công, đang khóa): `.github/workflows/deploy.yml`, `infra/deploy/legacy/setup-vps.sh`, `docs/operations/legacy-vps-deploy.md`.
- Baseline OpenAPI: `docs/api/legacy/admin-server.openapi.json`. Kế hoạch gỡ: `docs/operations/production-cutover-runbook.md` §8.
