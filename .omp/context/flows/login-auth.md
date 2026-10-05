# Flow: Đăng nhập & phiên (Login/Auth)
<!-- Nguồn: admin-web/src/{LoginPage.tsx,auth.tsx,api.ts} → admin-server/{main.py,auth.py,db.py} → reward-service/{api/auth_proxy.py,security.py} -->

## Entry Points

| Method | Path | File | Middleware |
|---|---|---|---|
| POST | `/v1/auth/login` | `admin-server/auth.py:305 login` | rate-limit 5/300s theo IP |
| POST | `/v1/auth/refresh` | `admin-server/auth.py:351 refresh` | none |
| POST | `/v1/auth/logout` | `admin-server/auth.py:367 logout` | none |
| GET | `/v1/auth/me` | `admin-server/auth.py:376 me` | Bearer JWT |
| POST | `/v1/reward/auth/login` | `reward-service/api/auth_proxy.py:67-70` | proxy nguyên trạng sang admin-server, kèm `X-Real-IP` |
| GET | `/v1/reward/auth/me` | `reward-service/api/auth_proxy.py:82-101` | Bearer JWT, tra thêm `accounts` để làm giàu role/status |

## Luồng chính (Client → admin-server)

1. **Submit form** — `admin-web/src/routes/LoginPage.tsx:44-56 handleSubmit`
   - Input: username, password, checkbox "remember"
   - Gọi `AuthProvider.login()`

2. **Client gọi API** — `admin-web/src/auth.tsx:184-194 login` → `admin-web/src/api.ts:154-160 authApi.login`
   - `apiFetch(POST /v1/auth/login, {username,password})`

3. **Server xử lý** — `admin-server/auth.py:305-350 login`
   - `_check_rate(ip,"login",5,300)` — 429 nếu vượt.
   - `db.get_account_by_username(username)` (`db.py:721`).
   - Sai username/password → sleep 1s (chống timing attack) → 401.
   - `status=='pending' và !email_verified` → 403 "chưa xác thực email".
   - `status=='suspended'` → 403 "đã bị khoá".
   - OK → `_create_access_token(id,username,role)` (JWT HS256, TTL 15 phút) +
     `db.create_refresh_token(id, 30 ngày)` (ghi bảng `refresh_tokens`) + `db.update_last_login(id)`.
   - Output: `{access_token, refresh_token, expires_in, user:{id,username,email,role}}`.

4. **Client lưu token** — `admin-web/src/auth.tsx:186-192`
   - `setTokens(access, refresh)` → access token **CHỈ lưu biến JS memory** (`api.ts:8 _accessToken`),
     mất khi F5.
   - Refresh token → `localStorage` (nếu remember) hoặc `sessionStorage`, key `admin_refresh_token`.
   - `setUser(d.user)` → `useEffect` điều hướng `/dashboard` (hoặc `/reward/jobs` nếu role=discord, xem
     `RoleGate.tsx:HomeRedirect`).

## Khởi tạo lại phiên khi mở app (F5 / mở tab mới)

`admin-web/src/auth.tsx:161-180` — đọc `admin_refresh_token` từ storage → `authApi.refresh(stored)`
(`POST /v1/auth/refresh`) → `setTokens` → `authApi.me()` (`GET /v1/auth/me`) → `setUser`. Không có
refresh token hợp lệ → ở lại `/login`.

## Auto-refresh khi access token hết hạn (401 giữa chừng)

`admin-web/src/api.ts:39-98,110-119` — `apiFetch`/`apiFetchRaw` bắt 401 → `tryRefresh()` (gộp nhiều
request đồng thời bằng 1 Promise `_refreshing`) → thành công thì retry request gốc; thất bại →
`_onSessionExpired()` → `AuthProvider` set `sessionExpired=true` →
`ProtectedRoute.tsx:31` phủ overlay `SessionExpired.tsx` (không redirect đột ngột).

## Đăng xuất

- Thủ công: `auth.tsx:196-199 doLogout()` → `POST /v1/auth/logout` (`db.revoke_refresh_token`) →
  `clearTokens()` + xoá storage → `navigate('/login')`.
- Đa tab: lắng nghe `storage` event (`auth.tsx:145-157`) — tab khác xoá key thì tab này cũng clear.
- Inactivity: 60 phút không thao tác → cảnh báo phút 55 (`SessionWarning.tsx`, đếm ngược 5 phút) → tự
  gọi logout.

## Refresh token (chi tiết backend)

`POST /v1/auth/refresh` (`auth.py:351`) → `db.get_refresh_token()` → check `account.status=='active'` →
cấp **access token mới** (KHÔNG cấp refresh mới, refresh token dùng lại tới khi hết 30 ngày hoặc bị
revoke).

## Auth phía reward-service (proxy + verify local)

- `/v1/reward/auth/login|refresh|logout` — **proxy nguyên trạng** sang admin-server (không có state
  auth riêng ở reward-service), chỉ thêm header `X-Real-IP`.
- `/v1/reward/auth/me` — verify JWT bằng `security.py:decode_access_token` (cùng `JWT_SECRET`, thuật
  toán HS256, payload `{sub,username,role,exp,iat}` PHẢI khớp admin-server), sau đó `SELECT accounts`
  làm giàu thêm `status`/`reward_access` — đây là điểm T2 (tra DB) đầu tiên sau khi có JWT.
- Mọi route ghi của reward-service dùng `require_active_account` (T2) — tra lại role/status DB mỗi lần,
  tránh JWT còn hạn dùng được sau khi bị revoke/suspend giữa chừng (khác admin-server, xem RULES.md).

## Xử lý lỗi

| Điều kiện | Response |
|---|---|
| Sai username/password | 401 (sau delay 1s) |
| Account `pending`, email chưa verify | 403 |
| Account `suspended` | 403 |
| Vượt rate limit login (5/300s/IP) | 429 |
| Refresh token invalid/hết hạn/account không active | 401 |
| JWT reward-service lệch JWT_SECRET với admin-server | verify fail → 401 (phát hiện sớm qua `scripts/check-env.py` lúc deploy) |

## Side Effects

- Ghi `refresh_tokens` (tạo lúc login, revoke lúc logout/reset-password/change-password).
- Ghi `accounts.last_login`.
- Email verify/reset gửi qua `email_service.py` (SMTP) — không phải side effect của login nhưng dùng
  chung cơ chế token với luồng đổi mật khẩu (mục "Đăng ký & xác thực" — xem `admin_api.py`/`auth.py` nếu
  cần trace riêng, không lặp lại ở đây).

## Dependencies

- Module: `admin-server/{auth.py,db.py,email_service.py}`, `admin-web/src/{auth.tsx,api.ts,routes/LoginPage.tsx}`, `reward-service/{api/auth_proxy.py,security.py}`.
- Database tables: `accounts`, `refresh_tokens` (admin-server sở hữu).
