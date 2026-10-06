# 5. Xác thực và bảo mật

Bản hợp đồng chuẩn: `docs/api/service-auth-contract-v1.md`. Sơ đồ đăng nhập/thu hồi: `docs/architecture/rp-login-revocation-sequence.html`.

## 5.1 Ba mặt phẳng xác thực (độc lập nhau)

| Mặt phẳng | Ai | Credential | Ai xác thực | Mô hình quyền |
|---|---|---|---|---|
| **User** | Người dùng web | Access token **RS256** (JWT) do Identity ký | **Mọi** service tự verify offline bằng JWKS (`manager_sdk.TokenVerifier`) | **Role** (`viewer`, `admin`, `dash_admin`, `discord`-shim) + **permission** (`perms`) |
| **Machine** | Tool / Fallback Tool | API key `mpk_<key_id>.<secret>` (+ `client_id`) | **Chỉ Integration** (lưu hash; hiện đúng 1 lần) | **Scope** (`snapshots:write`, `jobs:claim`, …) + `allowed_platforms` |
| **Service-to-service** | Service ↔ service | Header `X-Service-Key` (khóa **riêng từng cặp nguồn→bên đọc**, ≥24 ký tự) | Service phát feed | Chỉ `/internal/v1/*`; **gateway không bao giờ route** |

**Role và Scope là hai mô hình độc lập** (luật 7): API key của Tool không dùng được cho API người dùng và ngược lại (E2E kiểm: key máy → `401` ở Analytics, token user → `401/403` ở ingest máy).
**Không có một JWT secret đối xứng chung** (luật 8). Chế độ HS256 chỉ tồn tại để sống chung legacy khi cần (`IDENTITY_ACCESS_TOKEN_ALG=HS256`) và là việc tạm của Phase 1.3.

## 5.2 Access token

Claim: `iss, aud (["manager-api"]), sub, username, role, perms, client_type, sid, jti, iat, nbf, exp`. Header `typ=at+jwt` **bắt buộc** (id_token/logout_token cùng khóa ký không thay thế được). Leeway 30 s. TTL hiện 900 s (D12 muốn 600 — xem [07](07-van-hanh-va-lo-trinh.md)).
Verifier từ chối: sai `iss/aud`, hết hạn, `alg=none`, HS256/HS512 khi cấu hình RS256, `kid` lạ, ký bằng khóa khác, header sai.
JWKS được cache với stale-while-error và `min_refetch` (chống bão fetch). **Khởi động lạnh khi Identity chết ⇒ chưa có khóa ⇒ 401** cho tới khi Identity sống lại; token đã cấp chỉ verify được khi JWKS đã từng được tải.

## 5.3 Khóa ký và xoay

- Khóa private RSA lưu trong `signing_keys`, **mã hóa Fernet** bằng `IDENTITY_KEY_ENCRYPTION_KEY` (nằm ngoài DB ⇒ dump DB không đủ để ký token). Đúng **một** khóa `active` (chỉ mục unique một phần); khóa `retired` vẫn publish trong `IDENTITY_JWKS_GRACE_SECONDS`.
- Xoay: `python -m identity_service.keys rotate` (và `list`). Một DB DEV chứa khóa ký mã hóa bằng Fernet **khác** sẽ làm Identity trả 500 khi ký (`InvalidToken`) — dựng lại DB DEV (`DevStack.reset_data()`) hoặc dùng đúng khóa.

## 5.4 Phiên và thu hồi

- Mỗi đăng nhập tạo `sessions` (`sid`); phiên SSO là `client_type='sso'`. Logout/khóa/xóa/đổi mật khẩu/đổi role ⇒ thu hồi `sid` **ngay tại Identity** (mọi endpoint của Identity và introspection từ chối).
- Service khác verify offline ⇒ độ trễ thu hồi tại đó ≤ TTL token **cộng** event `session.revoked`/`account.status_changed`… qua feed. Reward và Integration giữ **denylist/projection** từ feed để chặn ngay cả khi token còn hạn (Reward: dừng mọi job của tài khoản).
- Thao tác nhạy cảm nên dùng **introspection fail-closed** (D13): endpoint có, **chưa service nào dùng**.
- Back-channel logout: Identity gọi `backchannel_logout_uri` của RP đã đăng ký (chỉ https hoặc http loopback ở dev/test; cấm link-local/metadata; chặn RFC1918 mặc định; không theo redirect; dead-letter ngay nếu địa chỉ bị chặn).

## 5.5 OIDC

Public client `manager-web` (PKCE S256, redirect URI so khớp **chính xác**, không wildcard; `client_id`/`redirect_uri` sai ⇒ trang lỗi 400, không redirect). Code đốt ở mọi nhánh lỗi sau khi xác thực client; dùng lại code ⇒ thu hồi family. Client tạo qua Admin API mặc định third-party (qua màn consent); chỉ `admin` đặt `first_party`.
Đăng ký RP mới (website dùng đăng nhập chung): `POST /v1/admin/oidc/clients` hoặc `python -m identity_service.oidc_clients seed-dev` (chỉ DEV) — xem mục 2.9 của assessment.

## 5.6 Machine credential (Integration)

- Lưu **hash**, secret hiện đúng một lần, xoay (`/credentials/{key_id}/rotate`), thu hồi, TTL tùy chọn; `client.status` và `account.status` đều được kiểm khi xác thực; chặn dò key (30 lần sai/phút/IP chặn cả key đúng cùng IP trong phút đó); rate limit mặc định 600/phút (trong bộ nhớ).
- **Ownership suy ra từ credential**; `owner*` ở body ⇒ 422. Platform ngoài `allowed_platforms` ⇒ `403 PLATFORM_NOT_ALLOWED`; thiếu scope ⇒ `403 SCOPE_DENIED`.
- Cấp credential cần `email_verified` của owner. Đăng ký kèm Tool: `credential_status: pending_email_verification` cho tới khi xác thực.

## 5.7 Kiểm soát biên (gateway)

Chặn `/internal` mọi biến thể; xóa header nhạy cảm và spoof (`X-Service-Key`, `X-Forwarded-*`, `X-Real-IP`, method override); CORS allowlist; security headers; giới hạn body (2 MiB, 413 ngay cả chunked)/header/URL; Content-Length xung đột bị từ chối; rate limit; circuit breaker; access log che `Authorization`/query nhạy cảm; httpx/httpcore ép WARNING (vì log URL đầy đủ).

## 5.8 Quản lý secret

- **Không secret nào trong repo.** `bootstrap_dev.py` sinh `.env.dev` (gitignored) + render `.dev/` từ template; compose đọc secret từ env. `--rotate` sinh lại (cần dựng lại volume).
- Test quét secret trong file đã commit (`tests/arch/test_infra.py`): private key, secret gán cứng, DSN có mật khẩu, JWT secret mặc định cũ. Test nhắc chuỗi cấm phải tránh literal (ghép chuỗi) hoặc nằm trong allowlist có tài liệu.
- Web: quét `dist/` sau build; refresh token không vào `localStorage`; API key chỉ trong modal một lần.
- Nhật ký: không log mật khẩu/token/API key; lỗi validation không phản chiếu giá trị đầu vào; `logredact.py` che query nhạy cảm của uvicorn.
- Production: secret manager (chưa dựng) — xem runbook.

## 5.9 Lỗ hổng đã tìm thấy và sửa (để không tái phạm)

`admin-server` có JWT secret mặc định thật (nay: dev tự sinh, production từ chối khởi động — **đổi hành vi khi deploy**) · mật khẩu/secret test cứng · đếm lần thử đổi mật khẩu bị rollback cùng transaction · NUL byte ⇒ 500 · request treo 60 s khi DB chết · readiness treo ·
outbox đảo thứ tự commit làm mất event · deadlock khóa advisory/dòng giữa đường Tool và legacy · `display_name` legacy vượt giới hạn canonical · `pyjwt 2.14.0` (PYSEC-2026-4141 ⇒ `>=2.15.0`) · `pytest 8.4.2` (PYSEC-2026-1845, chỉ ảnh hưởng `/tmp` UNIX, dev-only ⇒ ngoại lệ có thời hạn) · web: refresh token trong `localStorage`.
Chi tiết: `architecture/implementation-decisions.md`, `operations/implementation-final-report.md` mục 7.

## 5.10 Giới hạn bảo mật đã biết

Rate limit/circuit breaker theo từng tiến trình (nhân lên theo số worker) · rate limit mặt phẳng user của Integration chưa có · slowloris ở header để uvicorn/nginx xử lý · refresh trượt chưa có hạn tuyệt đối · consent không ghi nhớ · introspection chưa được dùng cho thao tác nhạy cảm · `npm audit` còn 5 lỗ hổng dev-only (vite/vitest/esbuild; vitest critical chỉ khi bật UI server).
