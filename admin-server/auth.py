"""Endpoints xác thực cho admin web — đăng ký, đăng nhập, refresh, verify email, reset password.

12 endpoint, tất cả ở prefix `/v1/auth`. Không endpoint nào cần token admin — viewer tự phục vụ.
Admin quản lý invite key và khoá tài khoản trực tiếp trong CSDL.

Rate limiting bằng bộ đếm in-memory (dict IP → timestamps). Process restart thì đếm lại — chấp
nhận được vì admin web chạy riêng, ít traffic, và mục đích chính là chặn brute-force tự động chứ
không phải DDoS.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import bcrypt
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field

if TYPE_CHECKING:
    from db import Db

logger = logging.getLogger("admin.auth")

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-thay-doi-khi-deploy")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = 15 * 60          # 15 phút
REFRESH_TOKEN_TTL_DAYS = 30

# Thời hạn token xác thực email (24h) và reset password (1h).
VERIFY_TOKEN_TTL = 24 * 3600
RESET_TOKEN_TTL = 3600

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8421")


# ── Rate limiting ───────────────────────────────────────────────────────────────
#
# Bộ đếm theo IP, không persist — restart process là reset. Đủ để chặn script brute-force password;
# không đủ để chống DDoS phân tán nhưng đó là việc của nginx/firewall.

_rate: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    # Nginx forward X-Real-IP; fallback request.client.host khi chay khong co proxy.
    # Khong dung X-Forwarded-For vi co the gia mao nhieu tang proxy.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _check_rate(ip: str, action: str, max_hits: int, window_s: int) -> None:
    """Nếu quá `max_hits` lần trong `window_s` giây gần nhất → 429."""
    key = f"{ip}:{action}"
    now = time.time()
    _rate[key] = [t for t in _rate[key] if t > now - window_s]
    if len(_rate[key]) >= max_hits:
        retry = int(window_s - (now - _rate[key][0]))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Quá nhiều lần thử. Đợi {retry} giây.",
            headers={"Retry-After": str(retry)},
        )
    _rate[key].append(now)


# ── JWT helpers ─────────────────────────────────────────────────────────────────

def _create_access_token(account_id: str, username: str, role: str) -> str:
    payload = {
        "sub": account_id,
        "username": username,
        "role": role,
        "exp": int(time.time()) + ACCESS_TOKEN_TTL,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_access_token(token: str) -> dict:
    """Giải JWT → dict payload. Ném 401 nếu hỏng hoặc hết hạn."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token hết hạn")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token không hợp lệ")


# ── Models ──────────────────────────────────────────────────────────────────────

class ValidateKeyBody(BaseModel):
    key: str

class CheckUsernameBody(BaseModel):
    username: str

class CheckEmailBody(BaseModel):
    email: str

class RegisterBody(BaseModel):
    key: str
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=8)
    email: EmailStr

class LoginBody(BaseModel):
    username: str
    password: str

class RefreshBody(BaseModel):
    refresh_token: str

class LogoutBody(BaseModel):
    refresh_token: str

class ForgotPasswordBody(BaseModel):
    email: EmailStr

class ResetPasswordBody(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)

class ResendVerifyBody(BaseModel):
    email: EmailStr

class SendChangeCodeBody(BaseModel):
    """Gửi mã xác thực 6 số về email hiện tại — bước 1 đổi mật khẩu."""
    pass  # Không cần gì — email lấy từ JWT

class ChangePasswordBody(BaseModel):
    """Bước 2: xác thực mã + nhập mật khẩu mới."""
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8)

class UpdateProfileBody(BaseModel):
    """User tự cập nhật hồ sơ."""
    email: EmailStr | None = None
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")


# ── Router factory ──────────────────────────────────────────────────────────────

def make_router(db: Db, send_verify_email=None, send_reset_email=None) -> APIRouter:
    """Nhận `db` và hai hàm gửi email (có thể None nếu chưa cấu hình SMTP).

    `send_verify_email(email, token, username)` và `send_reset_email(email, token, username)` là
    coroutine — gọi bằng `await`. Nếu None thì chỉ log token ra console (đủ cho dev/test).
    """
    router = APIRouter(prefix="/v1/auth", tags=["auth"])

    # ── Dependency: lấy account từ JWT ──────────────────────────────────────────

    async def get_current_user(authorization: str = Header("")) -> dict:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu token")
        payload = _decode_access_token(authorization[7:])
        account = await db.get_account_by_id(payload["sub"])
        if not account:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tài khoản không tồn tại")
        if account.get("status") == "suspended":
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Tài khoản đã bị khoá. Liên hệ quản trị viên.")
        return account

    CurrentUser = Depends(get_current_user)

    # ── 1. Validate invite key ──────────────────────────────────────────────────

    @router.post("/validate-key", summary="Kiểm tra invite key hợp lệ không")
    async def validate_key(body: ValidateKeyBody, request: Request) -> dict:
        _check_rate(_client_ip(request), "validate-key", 10, 60)
        row = await db.get_invite_key(body.key.strip())
        if not row:
            return {"valid": False, "reason": "Mã mời không hợp lệ"}
        if row["status"] != "active":
            return {"valid": False, "reason": "Mã mời đã bị vô hiệu"}
        if row["used_count"] >= row["max_uses"]:
            return {"valid": False, "reason": "Mã mời đã hết lượt sử dụng"}
        # Kiểm tra hết hạn — key cũ không có expires_at thì bỏ qua
        expires_at = row.get("expires_at") or ""
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp < datetime.now(timezone.utc):
                    return {"valid": False, "reason": "Mã mời đã hết hạn"}
            except ValueError:
                pass
        return {"valid": True, "role": row["role"]}

    # ── 2. Check username ───────────────────────────────────────────────────────

    @router.post("/check-username", summary="Username đã có ai dùng chưa")
    async def check_username(body: CheckUsernameBody, request: Request) -> dict:
        _check_rate(_client_ip(request), "check", 30, 60)
        return {"available": not await db.username_exists(body.username.strip())}

    # ── 3. Check email ──────────────────────────────────────────────────────────

    @router.post("/check-email", summary="Email đã đăng ký chưa")
    async def check_email(body: CheckEmailBody, request: Request) -> dict:
        _check_rate(_client_ip(request), "check", 30, 60)
        return {"available": not await db.email_exists(body.email.strip())}

    # ── 4. Register ─────────────────────────────────────────────────────────────

    @router.post("/register", summary="Đăng ký tài khoản mới bằng invite key")
    async def register(body: RegisterBody, request: Request) -> dict:
        _check_rate(_client_ip(request), "register", 3, 3600)

        # Validate key — phải còn lượt
        key_row = await db.get_invite_key(body.key.strip())
        if not key_row or key_row["status"] != "active" or key_row["used_count"] >= key_row["max_uses"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mã mời không hợp lệ hoặc đã hết lượt")

        # Kiểm trùng
        if await db.username_exists(body.username.strip()):
            raise HTTPException(status.HTTP_409_CONFLICT, "Tên đăng nhập đã được sử dụng")
        if await db.email_exists(body.email.strip()):
            raise HTTPException(status.HTTP_409_CONFLICT, "Email đã được đăng ký")

        # Dùng key (atomic check+increment)
        if not await db.use_invite_key(body.key.strip()):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mã mời đã hết lượt sử dụng")

        # Hash password — bcrypt cost 12
        pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt(rounds=12)).decode()

        account = await db.create_account(
            username=body.username.strip(),
            email=body.email.strip(),
            password_hash=pw_hash,
            role=key_row["role"],
            invite_key=body.key.strip(),
        )

        # Gửi email xác thực
        verify_token = account["verify_token"]
        if send_verify_email:
            try:
                await send_verify_email(account["email"], verify_token, account["username"])
            except Exception:
                logger.warning("Không gửi được email xác thực cho %s", account["email"],
                               exc_info=True)
        else:
            logger.info("Email verify token cho %s: %s", account["email"], verify_token)

        return {"ok": True, "email": account["email"],
                "message": "Đã gửi email xác thực. Kiểm tra hộp thư để kích hoạt tài khoản."}

    # ── 5. Verify email ─────────────────────────────────────────────────────────

    @router.get("/verify-email", summary="Xác thực email (click link từ email)")
    async def verify_email(token: str = Query(...)) -> dict:
        account = await db.get_account_by_verify_token(token)
        if not account:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Link xác thực không hợp lệ hoặc đã được sử dụng")

        # Kiểm hạn 24h
        issued_at = datetime.fromisoformat(account["verify_token_at"])
        if (datetime.now(timezone.utc) - issued_at).total_seconds() > VERIFY_TOKEN_TTL:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Link xác thực đã hết hạn. Đăng nhập và yêu cầu gửi lại.")

        await db.set_email_verified(account["id"])
        # Frontend sẽ redirect về login với query param verified=true
        return {"ok": True, "message": "Xác thực email thành công. Đăng nhập ngay."}

    # ── 6. Resend verify email ──────────────────────────────────────────────────

    @router.post("/resend-verify", summary="Gửi lại email xác thực")
    async def resend_verify(body: ResendVerifyBody, request: Request) -> dict:
        _check_rate(_client_ip(request), "resend", 5, 3600)

        account = await db.get_account_by_email(body.email.strip())
        # KHÔNG tiết lộ email có tồn tại không — luôn trả "đã gửi"
        if not account or account.get("email_verified") or account.get("status") == "suspended":
            return {"ok": True, "message": "Nếu email tồn tại và chưa xác thực, đã gửi lại."}

        new_token = uuid.uuid4().hex
        await db.set_verify_token(account["id"], new_token)

        if send_verify_email:
            try:
                await send_verify_email(account["email"], new_token, account["username"])
            except Exception:
                logger.warning("Không gửi được email xác thực cho %s", account["email"],
                               exc_info=True)
        else:
            logger.info("Resend verify token cho %s: %s", account["email"], new_token)

        return {"ok": True, "message": "Nếu email tồn tại và chưa xác thực, đã gửi lại."}

    # ── 7. Login ────────────────────────────────────────────────────────────────

    @router.post("/login", summary="Đăng nhập bằng username + password")
    async def login(body: LoginBody, request: Request) -> dict:
        _check_rate(_client_ip(request), "login", 5, 300)

        account = await db.get_account_by_username(body.username.strip())

        # Delay 1 giây khi sai — chống timing attack và brute-force. Delay CẢ khi username không
        # tồn tại để attacker không phân biệt được "sai username" vs "sai password" qua thời gian
        # phản hồi.
        if not account or not bcrypt.checkpw(body.password.encode(),
                                             account["password_hash"].encode()):
            await asyncio.sleep(1.0)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                "Tên đăng nhập hoặc mật khẩu không đúng")

        if account["status"] == "pending":
            # Chưa verify email — KHÔNG cho đăng nhập, nhưng gợi ý gửi lại
            ev = account.get("email_verified")
            if not ev and ev != 1:
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    "Tài khoản chưa xác thực email. "
                                    "Kiểm tra hộp thư hoặc yêu cầu gửi lại email xác thực.")

        if account["status"] == "suspended":
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Tài khoản đã bị khoá. Liên hệ quản trị viên.")

        # Cấp token
        access = _create_access_token(account["id"], account["username"], account["role"])
        refresh = await db.create_refresh_token(account["id"], REFRESH_TOKEN_TTL_DAYS)
        await db.update_last_login(account["id"])

        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": ACCESS_TOKEN_TTL,
            "user": {
                "id": account["id"],
                "username": account["username"],
                "email": account["email"],
                "role": account["role"],
            },
        }

    # ── 8. Refresh token ────────────────────────────────────────────────────────

    @router.post("/refresh", summary="Đổi refresh token lấy access token mới")
    async def refresh(body: RefreshBody) -> dict:
        rt = await db.get_refresh_token(body.refresh_token)
        if not rt:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                "Refresh token không hợp lệ hoặc đã hết hạn")

        account = await db.get_account_by_id(rt["account_id"])
        if not account or account["status"] != "active":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tài khoản không khả dụng")

        access = _create_access_token(account["id"], account["username"], account["role"])
        return {"access_token": access, "expires_in": ACCESS_TOKEN_TTL}

    # ── 9. Logout ───────────────────────────────────────────────────────────────

    @router.post("/logout", summary="Đăng xuất — thu hồi refresh token")
    async def logout(body: LogoutBody) -> dict:
        """Fire-and-forget: luôn trả 200 dù token không tồn tại — caller đã muốn đăng xuất rồi,
        trả lỗi chỉ khiến frontend retry mãi."""
        await db.revoke_refresh_token(body.refresh_token)
        return {"ok": True}

    # ── 10. Me ──────────────────────────────────────────────────────────────────

    @router.get("/me", summary="Thông tin người dùng hiện tại")
    async def me(account: dict = CurrentUser) -> dict:
        return {
            "id": account["id"],
            "username": account["username"],
            "email": account["email"],
            "role": account["role"],
            "created_at": account.get("created_at", ""),
        }

    # ── 11. Forgot password ─────────────────────────────────────────────────────

    @router.post("/forgot-password", summary="Gửi email đặt lại mật khẩu")
    async def forgot_password(body: ForgotPasswordBody, request: Request) -> dict:
        _check_rate(_client_ip(request), "forgot", 3, 3600)

        # KHÔNG xác nhận email có tồn tại — tránh dò tài khoản
        account = await db.get_account_by_email(body.email.strip())
        if account and account["status"] == "active":
            reset_token = uuid.uuid4().hex
            await db.set_reset_token(account["id"], reset_token)

            if send_reset_email:
                try:
                    await send_reset_email(account["email"], reset_token, account["username"])
                except Exception:
                    logger.warning("Không gửi được email reset cho %s", account["email"],
                                   exc_info=True)
            else:
                logger.info("Reset token cho %s: %s", account["email"], reset_token)

        return {"ok": True, "message": "Nếu email tồn tại, đã gửi link đặt lại mật khẩu."}

    # ── 12. Reset password ──────────────────────────────────────────────────────

    @router.post("/reset-password", summary="Đặt lại mật khẩu bằng token từ email")
    async def reset_password(body: ResetPasswordBody) -> dict:
        account = await db.get_account_by_reset_token(body.token)
        if not account:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Link đặt lại mật khẩu không hợp lệ hoặc đã được sử dụng")

        # Kiểm hạn 1h
        issued_at = datetime.fromisoformat(account["reset_token_at"])
        if (datetime.now(timezone.utc) - issued_at).total_seconds() > RESET_TOKEN_TTL:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Link đặt lại mật khẩu đã hết hạn. Yêu cầu gửi lại.")

        pw_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt(rounds=12)).decode()
        await db.update_password(account["id"], pw_hash)
        await db.clear_reset_token(account["id"])
        # Thu hồi mọi refresh token — bắt đăng nhập lại ở mọi nơi
        await db.revoke_all_refresh_tokens(account["id"])

        return {"ok": True, "message": "Mật khẩu đã đặt lại. Đăng nhập với mật khẩu mới."}

    # ── 13. Gửi mã đổi mật khẩu ───────────────────────────────────────────────

    @router.post("/change-password/send-code",
                 summary="Gửi mã xác thực 6 số về email — bước 1 đổi mật khẩu")
    async def send_change_code(request: Request, account: dict = CurrentUser) -> dict:
        """Tạo mã 6 chữ số, lưu vào reset_token, gửi email.

        Dùng lại cặp reset_token + reset_token_at — tránh thêm cột. Mã 6 số ngắn hơn UUID
        nên dễ gõ, nhưng brute-force 10^6 = 1M nên rate limit chặt (3 lần / 10 phút).
        """
        _check_rate(_client_ip(request), "change-code", 3, 600)

        import random
        code = f"{random.randint(0, 999999):06d}"
        await db.set_reset_token(account["id"], code)

        if send_reset_email:
            try:
                # Gửi mã code thay vì link — template email cần nhận diện đây là code
                await send_reset_email(account["email"], code, account["username"])
            except Exception:
                logger.warning("Không gửi được mã xác thực cho %s", account["email"],
                               exc_info=True)
        else:
            logger.info("Change-password code cho %s: %s", account["email"], code)

        return {"ok": True, "message": "Đã gửi mã xác thực về email của bạn."}

    # ── 14. Xác thực mã + đổi mật khẩu ────────────────────────────────────────

    @router.post("/change-password",
                 summary="Xác thực mã 6 số + đặt mật khẩu mới — bước 2")
    async def change_password(body: ChangePasswordBody, request: Request,
                              account: dict = CurrentUser) -> dict:
        _check_rate(_client_ip(request), "change-pw", 5, 600)

        # Kiểm tra mã — lấy từ DB để so sánh an toàn
        fresh = await db.get_account_by_id(account["id"])
        if not fresh or not fresh.get("reset_token"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Chưa yêu cầu mã xác thực. Gửi mã trước.")

        # Kiểm thời gian — code hết hạn sau 10 phút
        issued_at = datetime.fromisoformat(fresh["reset_token_at"])
        if (datetime.now(timezone.utc) - issued_at).total_seconds() > 600:
            await db.clear_reset_token(account["id"])
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Mã xác thực đã hết hạn. Yêu cầu gửi lại.")

        # Timing-safe comparison — không cho phép đoán từng chữ số qua thời gian phản hồi
        if not hmac.compare_digest(body.code, fresh["reset_token"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mã xác thực không đúng")

        pw_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt(rounds=12)).decode()
        await db.update_password(account["id"], pw_hash)
        await db.clear_reset_token(account["id"])
        # Thu hồi refresh token ở mọi nơi — bắt đăng nhập lại bằng mật khẩu mới
        await db.revoke_all_refresh_tokens(account["id"])

        return {"ok": True, "message": "Mật khẩu đã thay đổi. Đăng nhập lại với mật khẩu mới."}

    # ── 15. Cập nhật hồ sơ ─────────────────────────────────────────────────────

    @router.patch("/profile", summary="User tự cập nhật hồ sơ (email, username)")
    async def update_profile(body: UpdateProfileBody, account: dict = CurrentUser) -> dict:
        updates: dict[str, str] = {}

        if body.username and body.username != account["username"]:
            if await db.username_exists(body.username):
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "Tên đăng nhập đã được sử dụng")
            updates["username"] = body.username

        if body.email and body.email.lower() != account["email"]:
            if await db.email_exists(body.email):
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "Email đã được đăng ký")
            updates["email"] = body.email.lower()

        if not updates:
            return {"ok": True, "message": "Không có thay đổi."}

        await db.update_account_profile(account["id"], updates)
        return {"ok": True, "message": "Đã cập nhật hồ sơ.", "updates": updates}

    return router
