# identity-service

Nguồn sự thật duy nhất cho account, đăng nhập/đăng ký, token, role, permission, registration key và OIDC.

| | |
|---|---|
| Cổng | 8430 |
| Database (riêng) | `identity_db` |
| Package | `identity_service` |
| Trạng thái | scaffold (chưa có nghiệp vụ; module được hiện thực theo phase) |

## Module

| Module | Phase | Trách nhiệm |
|---|---|---|
| `auth` | 1.2 | register, login, logout, refresh, verify-email, forgot/reset/change password |
| `accounts` | 1.3 | profile, quản trị user, trạng thái tài khoản |
| `registration_keys` | 1.3 | invite/registration key, integration_policy, allowed_apps |
| `tokens` | 1.4 | phát hành JWT RS256, JWKS, xoay khóa ký |
| `permissions` | 1.4 | role, permission catalog và grant |
| `oidc` | 1.6 | discovery, authorize, token, userinfo, introspection, back-channel logout, registry client |
| `audit` | 1.3 | audit đăng nhập và thay đổi quyền |
| `outbox` | 1.4 | transactional outbox và phát event account.* |
| `notifications` | 1.3 | adapter gửi email (verify, reset); interface tách được thành service riêng |

## Dữ liệu và sự kiện (theo `infra/service-registry.json`)

- Bảng sở hữu: `accounts`, `invite_keys`, `refresh_tokens`, `permissions`, `account_permissions`, `oidc_clients`, `signing_keys`, `auth_audit`, `outbox`
- Phát event: `account.registered`, `account.verified`, `account.status_changed`, `account.deleted`, `account.permission_changed`, `session.revoked`
- Nhận event: `integration.client.provisioned`, `integration.client.provision_failed`

## Chạy

```bash
cd services/identity-service
python -m pytest -q                  # test riêng của service
python -m uvicorn identity_service.main:app --port 8430
```

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
