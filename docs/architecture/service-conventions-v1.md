# Service Conventions v1 (bắt buộc cho mọi service mới)

Đọc cùng `CLAUDE.md`. Mẫu tham chiếu: `services/identity-service` (cấu trúc, `db.py`, `migrate.py`, test).

## Cấu trúc & code
- Package `src/<package>/<module>/` theo module khai trong `infra/service-registry.json`. Mỗi module có docstring header (`module/service/phase/status/responsibility/boundary`);
  module đã hiện thực → `status: active`. Không tạo thư mục module ngoài registry (test kiến trúc chặn).
- Lớp: `router` (HTTP) → `service` (nghiệp vụ) → `repository` (SQL). Repository nhận `conn`, chỉ chạm bảng của service. SQL luôn tham số hoá.
- Chassis: `manager_observability.create_service_app`; lỗi dùng `manager_contracts.ApiError` + `ErrorCode` (thêm mã mới vào `errors.py` nếu thiếu — việc này được phép).
- DB: sao chép mẫu `identity_service/db.py` (pool lười, timeout ngắn, `connection()/transaction()`) và `migrate.py` (advisory lock key RIÊNG của service, checksum).
  Migration `NNNN_ten.sql` trong `services/<s>/migrations/`, forward-only; **sửa migration đã áp dụng là cấm** (tạo file mới).
- Config: `<PREFIX>_*` (xem `config.py` của identity); thiếu config bắt buộc → `ConfigError` lúc khởi động; **không có secret mặc định**.
- `/health/ready` chỉ kiểm DB của chính service (+ migrate up to date). Không gọi service khác. Outbound HTTP luôn có timeout + retry/backoff có giới hạn.
- Log JSON, không log mật khẩu/token/API key/Authorization.
- Mọi lỗi 4xx/5xx có `error.code` ổn định; không lộ traceback.

## Cổng & biến môi trường
| Service | Cổng | Prefix |
|---|---|---|
| api-gateway | 8400 | `GATEWAY_` |
| identity | 8430 | `IDENTITY_` |
| integration | 8431 | `INTEGRATION_` |
| analytics | 8432 | `ANALYTICS_` |
| reward | 8422 | `REWARD_` |

Tất cả bind `127.0.0.1` trong DEV. Biến dùng chung từ `.env.dev` (đã sinh): `DEV_PG_*`, `DEV_ISSUER`, `DEV_AUDIENCE`, `*_FEED_KEY_*`.
Service nhận khoá feed bên đọc qua biến `<PREFIX>_<SRC>_FEED_KEY` (vd analytics: `ANALYTICS_IDENTITY_FEED_KEY`, `ANALYTICS_INTEGRATION_FEED_KEY`),
URL nguồn qua `<PREFIX>_<SRC>_URL` (vd `ANALYTICS_INTEGRATION_URL=http://127.0.0.1:8431`). Thiếu URL/khoá ⇒ consumer tắt (service vẫn chạy).

## Test
- Mỗi service `cd services/<s> && python -m pytest -q`. Test cần Postgres: dùng `infra/scripts/devenv.py` (xem `identity-service/tests/conftest.py`),
  SKIP có lý do khi thiếu hạ tầng; **không** viết cứng mật khẩu/secret (test quét secret sẽ đỏ). Secret test sinh ngẫu nhiên mỗi lần chạy.
- Test hành vi thật trên Postgres thật (không mock thứ cần kiểm). "Test của test": ca giả lập vi phạm cho mỗi quy tắc bảo mật.
- Lỗi tìm ra → sửa **nguyên nhân gốc**, không nới assertion/xoá/skip test.
- Contract test chéo service đặt ở `tests/contract/`, E2E nhiều tiến trình ở `tests/e2e/`, cách ly ở `tests/integration/`.

## Không được đụng (do điều phối viên sở hữu)
`infra/service-registry.json` (trừ `modules`/`owned_tables`/`emits`/`consumes` của CHÍNH service — được sửa), `CLAUDE.md`, `infra/scripts/test_all.py`, `packages/*` (trừ khi
được giao rõ), `docs/` (trừ file của service mình và `docs/api/openapi/<service>.v1.json`), `.github/`, `tests/arch/*`. Không commit, không push, không chạm Docker ngoài
stack `mp-dev-*`, không chạm VPS/CI-CD. Báo cáo cuối: file đã tạo/sửa, số test pass/fail, quyết định thiết kế, giới hạn còn lại.
