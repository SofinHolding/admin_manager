# identity-service

Nguồn sự thật duy nhất cho account, đăng nhập/đăng ký, token, role, permission, registration key và OIDC.

| | |
|---|---|
| Cổng | 8430 |
| Database (riêng) | `identity_db` |
| Package | `identity_service` |
| Trạng thái | Phase 1.1–1.7 hiện thực (chưa cutover production — xem `docs/operations/identity-migration-runbook.md`) |

## Module

| Module | Phase | Trách nhiệm |
|---|---|---|
| `auth` | 1.2 | register (kèm integration tuỳ chọn), login, logout, refresh, verify-email, forgot/reset/change password |
| `accounts` | 1.2 | profile, quản trị user, `GET /v1/accounts/me/integration` |
| `registration_keys` | 1.2/1.7 | invite key, `integration_policy`, `allowed_apps` |
| `tokens` | 1.4 | phát hành JWT RS256, JWKS, xoay khóa ký (`python -m identity_service.keys list\|rotate`) |
| `permissions` | 1.4 | role, permission catalog và grant |
| `oidc` | 1.6 | discovery, authorize (+PKCE), trang đăng nhập hosted, token, userinfo, introspect, revoke, RP-initiated logout, back-channel logout, Admin API client |
| `feed_consumer` | 1.7 | kéo feed Integration → trạng thái provisioning |
| `audit` | 1.2 | audit đăng nhập và thay đổi quyền |
| `outbox` | 1.4 | transactional outbox và feed nội bộ `/internal/v1/feed` |
| `notifications` | 1.2 | adapter gửi email (verify, reset) |

## OIDC / OAuth2 (Phase 1.6)

Endpoint (đều theo chuẩn; mã lỗi RFC 6749/6750/7009/7662):

| Endpoint | Ghi chú |
|---|---|
| `GET /.well-known/openid-configuration`, `GET /.well-known/jwks.json` | discovery, JWKS (RS256, `kid`) |
| `GET /oauth/authorize` | `response_type=code`; **`redirect_uri` so khớp CHÍNH XÁC**, sai ⇒ trang lỗi (không redirect); `state` bắt buộc; PKCE `S256` bắt buộc cho public client (cấm `plain`) |
| `GET/POST /oauth/login` | trang đăng nhập hosted: CSRF double-submit, cookie `HttpOnly; SameSite`, `Secure`+`__Host-` khi issuer https, `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`, rate limit, không lộ username tồn tại |
| `POST /oauth/token` | `authorization_code` (+`code_verifier`) và `refresh_token` (xoay vòng theo family; dùng lại token đã xoay ⇒ thu hồi cả family + phiên) |
| `GET/POST /oauth/userinfo` | Bearer; claim theo scope + `role`, `perms` |
| `POST /oauth/introspect` | RFC 7662, **bắt buộc xác thực client confidential**; `active` theo trạng thái SỐNG (phiên + tài khoản) |
| `POST /oauth/revoke` | RFC 7009 |
| `GET/POST /oauth/logout` | RP-initiated; `post_logout_redirect_uri` so khớp chính xác; không có `id_token_hint` ⇒ cần xác nhận (CSRF) |
| `/v1/admin/oidc/clients` | Admin API quản trị client (secret chỉ hiển thị một lần) |

- Auth code: lưu **hash**, TTL 60s, dùng một lần; dùng lại ⇒ `invalid_grant` + thu hồi mọi thứ cấp từ code (RFC 6749 §4.1.2).
- Client first-party bỏ qua consent; client khác phải qua màn consent (không ghi nhớ).
- Phiên OIDC dùng chung bảng `sessions` ⇒ đăng xuất/khoá/đổi mật khẩu/đổi role có hiệu lực nhất quán với `/v1/auth/*`.
- **Back-channel logout** (OIDC Back-Channel Logout 1.0): worker nền đọc outbox của chính identity (`oidc_cursor`), gửi `logout_token`
  (RS256, `typ=logout+jwt`, không `nonce`) tới `backchannel_logout_uri`; retry backoff + jitter, hết lượt ⇒ `dead` trong
  `oidc_backchannel_deliveries`; SSRF guard (chỉ https, hoặc http loopback ở dev/test; cấm link-local/metadata/unspecified;
  kết nối thẳng tới IP đã kiểm; không theo redirect; timeout ngắn).
- Dev: `python -m identity_service.oidc_clients seed-dev` tạo client first-party `manager-web` (public) với redirect URI từ
  `IDENTITY_WEB_REDIRECT_URIS` (mặc định chỉ `http://127.0.0.1:5174/admin/callback`).
- Access token OIDC có `aud=[manager-api, <audience của client>]`, `client_id`, `scope`, `sid`; id_token `aud=client_id`.
  Chỉ access token (`typ=at+jwt`) được các endpoint API chấp nhận; id_token/logout_token cùng khoá ký không dùng thay được.

## Đăng ký kèm Integration (Phase 1.7)

`POST /v1/auth/register` nhận thêm `integration: {create, tool_name, client_kind, platforms[], scopes?[]}` (tuỳ chọn). Validate
**đồng bộ, cục bộ** theo `integration_policy` của key (`allow_integration_registration`, `allowed_platforms`, `allowed_scopes`,
`max_integration_clients`): sai ⇒ lỗi `PLATFORM_NOT_ALLOWED`/`SCOPE_DENIED`/`INTEGRATION_NOT_ALLOWED`/`INTEGRATION_LIMIT_EXCEEDED`,
**không tạo account, không tiêu key**. Đúng ⇒ một transaction: account + `integration_provisioning` (client_id `cli_` + 24 hex)
+ outbox `account.registered` kèm `provisioning`. Không yêu cầu ⇒ response y hệt legacy.
`allowed_apps` trên key tự cấp permission `<app>:access` (app phải có trong catalog). Consumer (`feed_consumer`) kéo
`integration.client.provisioned|provision_failed` từ `IDENTITY_INTEGRATION_FEED_URL`/`_KEY` (thiếu ⇒ tắt); con trỏ bền,
idempotent theo event id, retry/backoff, dead-letter (`python -m identity_service.feed_consumer replay`).

## Dữ liệu và sự kiện (theo `infra/service-registry.json`)

- Bảng sở hữu: `accounts`, `invite_keys`, `refresh_tokens`, `permissions`, `account_permissions`, `signing_keys`, `sessions`, `auth_audit`,
  `outbox`, `oidc_clients`, `sso_sessions`, `oidc_auth_codes`, `oidc_refresh_tokens`, `oidc_session_clients`,
  `oidc_backchannel_deliveries`, `oidc_cursor`, `integration_provisioning`, `feed_cursor`, `processed_events`, `dead_letters`
- Phát event: `account.registered`, `account.verified`, `account.status_changed`, `account.deleted`, `account.permission_changed`, `session.revoked`
- Nhận event: `integration.client.provisioned`, `integration.client.provision_failed`

## Chạy

```bash
cd services/identity-service
python -m identity_service.migrate          # job riêng, không chạy ngầm lúc khởi động
python -m pytest -q                         # test riêng của service (Postgres DEV thật)
python -m uvicorn identity_service.main:app --port 8430
```

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
