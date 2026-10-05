# Service Auth Contract v1

Nguồn sự thật cho xác thực giữa mọi thành phần. Mọi service và SDK tuân theo; thay đổi không tương thích = contract mới.

## 1. Ba mặt phẳng xác thực (độc lập)

| Mặt phẳng | Chủ thể | Credential | Verify ở đâu | Quyền |
|---|---|---|---|---|
| User | người dùng web/desktop | Access token JWT **RS256** do Identity ký | **Mọi service, offline** bằng JWKS | `role` + `perms` |
| Machine | Tool / Fallback Tool | API key `mpk_<key_id>.<secret>` do **Integration** cấp | **Chỉ Integration** | `scopes` + `allowed_platforms` |
| Service-to-service | service gọi `/internal/v1/*` | Header `X-Service-Key` (mỗi cặp nguồn→đọc một khoá) | Service nguồn | chỉ đọc feed/export |

Không dùng chéo: token user không phải credential máy, API key không phải token user, service key không gọi được API công khai.

## 2. Access token (user plane)

- Header JWT: `{"alg":"RS256","kid":"<kid>","typ":"at+jwt"}`. **Chỉ chấp nhận RS256.** Từ chối `none`, HS*, kid lạ.
- Claims: `iss` (URL Identity, vd `http://127.0.0.1:8430`), `aud` (mảng chứa `manager-api`), `sub` (account id, 32 hex),
  `username`, `role` (`viewer|admin|dash_admin|discord`), `perms` (mảng `"<ns>:<name>"`), `client_type` (`web|local_fallback_tool`),
  `sid` (session id), `jti`, `iat`, `nbf`, `exp` (mặc định +900s).
- Verify bắt buộc: chữ ký theo `kid`, `iss` khớp **chính xác**, `aud` chứa audience của service, `exp`/`nbf` (leeway 30s).
- `role` là phân quyền thô (admin quản trị). `perms` là quyền mịn theo domain (`reward:access`, `reward:admin`).
  Quy tắc: `role=admin` được coi là có mọi quyền quản trị; `dash_admin` quản trị dashboard/user nhưng không cấp quyền admin.
- Tương thích: tài khoản `role=discord` (legacy) mặc định có `reward:access`.

## 3. JWKS

- `GET {issuer}/.well-known/jwks.json` — công khai, `Cache-Control: public, max-age=300`.
- Chứa khoá active **và** khoá retired còn trong thời gian ân hạn (token đã phát vẫn verify được sau rotation).
- Khoá private nằm trong `identity_db.signing_keys`, mã hoá Fernet bằng `IDENTITY_KEY_ENCRYPTION_KEY`. Service khác chỉ thấy JWKS.
- Rotation: `python -m identity_service.keys rotate` (khoá mới active, khoá cũ retired nhưng vẫn publish ≥ TTL token + 5 phút).
- Verifier cache JWKS trong bộ nhớ (refresh mỗi ≤5 phút, và khi gặp `kid` lạ nhưng tối đa 1 lần/30s). **Identity chết:** vẫn verify
  token bằng khoá đã cache (tối đa 24h); `kid` lạ khi không lấy được JWKS → `401 AUTH_INVALID`, không crash.

## 4. API key máy (machine plane)

- Định dạng `mpk_<key_id>.<secret>`: `key_id` 12 ký tự `[A-Za-z0-9_-]`, `secret` 43 ký tự (256 bit).
- DB chỉ lưu `key_id` + `sha256(secret)`. Plaintext **chỉ trả một lần** lúc cấp/xoay. Có `last_used_at`, `expires_at` tuỳ chọn, thu hồi tức thì.
- Header: `Authorization: Bearer mpk_...`, `X-Contract-Version: 2026-10-01` (tuỳ chọn; có mà không hỗ trợ → `CONTRACT_VERSION_UNSUPPORTED`),
  `Idempotency-Key` cho mutation.
- **Ownership suy từ credential** (`owner_account_id` của client). Mọi trường owner trong payload bị schema từ chối.
- Scopes: `integration:handshake`, `resources:write`, `snapshots:write`, `metrics:write`, `jobs:claim`, `jobs:complete`,
  `jobs:claim:system` (chỉ `client_kind=fallback_tool` do admin cấp). Scope/platform được enforce ở service đích.

## 5. Service key (internal)

- Chỉ cho đường dẫn `/internal/v1/*`. Header `X-Service-Key: <key>`. So sánh hằng-thời-gian. Sai/thiếu → `401 AUTH_INVALID`.
- Biến môi trường ở service nguồn: `<SRC>_FEED_KEY_<READER>` (vd `IDENTITY_FEED_KEY_ANALYTICS`); ở bên đọc: khoá tương ứng cấu hình trong
  biến của chính nó (vd `ANALYTICS_IDENTITY_FEED_KEY`). Sinh ngẫu nhiên bởi `infra/scripts/bootstrap_dev.py`.
- Gateway **không bao giờ route** `/internal/*`. Test kiến trúc kiểm tra điều này.
- Không log khoá. Khoá khác nhau cho từng bên đọc để thu hồi riêng.

## 6. Mã lỗi

`AUTH_INVALID` (401), `AUTH_REVOKED` (401), `SCOPE_DENIED` (403), `PLATFORM_NOT_ALLOWED` (403), `CONTRACT_VERSION_UNSUPPORTED` (400),
`RATE_LIMITED` (429), `UNAVAILABLE` (503). API mới trả envelope `{ok:false,error:{code,message,details},meta:{request_id}}`;
endpoint tương thích legacy giữ `{"detail": ...}` (xem `route-map.json`, mục contract_negotiation).

## 7. SDK

`packages/sdk-python/manager_sdk`: `TokenVerifier` (JWKS + cache + offline), `Principal`, dependency FastAPI `require_user`,
`require_role`, `require_perm`, `ServiceKeyAuth`. Service dùng SDK, không tự cài verify.
