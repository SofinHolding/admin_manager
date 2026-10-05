"""Nghiệp vụ xác thực: đăng ký, đăng nhập, refresh, xác thực email, quên/đặt lại/đổi mật khẩu, hồ sơ.

Giữ NGUYÊN hành vi nhìn thấy từ ngoài (đường dẫn, body, thông điệp, mã HTTP) của admin-server cũ để frontend
không đổi. Các khác biệt CÓ CHỦ ĐÍCH (đều có test) so với legacy:
  1. Dùng key + tạo account trong MỘT transaction (legacy: key bị tiêu hao dù tạo account lỗi).
  2. Key hết hạn không đăng ký được (legacy chỉ kiểm hạn ở validate-key).
  3. Link xác thực email không mở khóa tài khoản đang bị suspended.
  4. Mã đổi mật khẩu tách khỏi token reset, giới hạn 5 lần thử (legacy: dò được qua /reset-password).
  5. Đăng nhập sai không lộ username có tồn tại qua thời gian phản hồi (so với hash giả).
  6. Mọi token lưu dạng hash; băm bcrypt chạy trong thread để không chặn event loop.
  7. Mã đổi mật khẩu có email riêng (legacy gửi nhầm bằng mẫu "link reset").
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import asyncpg

from identity_service.accounts import repository as accounts
from identity_service.audit import repository as audit
from identity_service.auth import legacy_jwt, refresh_repository as refresh_tokens
from identity_service.config import Settings
from identity_service.db import Database
from identity_service.errors import DomainError
from identity_service.notifications.port import Mailer
from identity_service.registration_keys import repository as keys
from identity_service.security import (hash_password, hash_token, iso, new_numeric_code, new_token,
                                       utcnow, verify_password)

logger = logging.getLogger("identity.auth")

VERIFY_TOKEN_TTL = timedelta(hours=24)
RESET_TOKEN_TTL = timedelta(hours=1)
CHANGE_CODE_TTL = timedelta(minutes=10)
MAX_CHANGE_CODE_ATTEMPTS = 5
_FALLBACK_TOOL = "local_fallback_tool"
_FALLBACK_ROLES = ("admin", "dash_admin")
_dummy_hash: str | None = None


async def _dummy_password_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await asyncio.to_thread(hash_password, "khong-ton-tai")
    return _dummy_hash


class AuthService:
    def __init__(self, db: Database, settings: Settings, mailer: Mailer, *,
                 now: Callable[[], datetime] = utcnow,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.db = db
        self.settings = settings
        self.mailer = mailer
        self.now = now
        self._sleep = sleep

    # ── tiện ích ────────────────────────────────────────────────────────────────

    def _access_token(self, account: dict, client_type: str = "web") -> str:
        return legacy_jwt.create_access_token(
            self.settings.jwt_compat_secret, account_id=account["id"], username=account["username"],
            role=account["role"], client_type=client_type, ttl_s=self.settings.access_token_ttl_s)

    def decode_access_token(self, token: str) -> dict:
        return legacy_jwt.decode_access_token(self.settings.jwt_compat_secret, token)

    async def _safe_send(self, send: Callable[..., Awaitable[None]], email: str, secret: str,
                         username: str) -> None:
        try:
            await send(email, secret, username)
        except Exception:  # noqa: BLE001 — gửi mail hỏng không được làm hỏng request (như legacy)
            logger.warning("Không gửi được email cho %s", email, exc_info=True)

    # ── invite key / kiểm tra trùng ─────────────────────────────────────────────

    async def validate_key(self, key: str) -> dict:
        async with self.db.connection() as conn:
            row = await keys.get(conn, key.strip())
        if not row:
            return {"valid": False, "reason": "Mã mời không hợp lệ"}
        if row["status"] != "active":
            return {"valid": False, "reason": "Mã mời đã bị vô hiệu"}
        if row["used_count"] >= row["max_uses"]:
            return {"valid": False, "reason": "Mã mời đã hết lượt sử dụng"}
        if row["expires_at"] is not None and row["expires_at"] < self.now():
            return {"valid": False, "reason": "Mã mời đã hết hạn"}
        return {"valid": True, "role": row["role"]}

    async def username_available(self, username: str) -> dict:
        async with self.db.connection() as conn:
            return {"available": not await accounts.username_exists(conn, username.strip())}

    async def email_available(self, email: str) -> dict:
        async with self.db.connection() as conn:
            return {"available": not await accounts.email_exists(conn, email.strip())}

    # ── đăng ký ─────────────────────────────────────────────────────────────────

    async def register(self, *, key: str, username: str, password: str, email: str) -> dict:
        key, username, email = key.strip(), username.strip(), email.strip()
        now = self.now()
        pw_hash = await asyncio.to_thread(hash_password, password)
        verify_token = new_token()

        async with self.db.transaction() as conn:
            key_row = await keys.get(conn, key)
            if (not key_row or key_row["status"] != "active"
                    or key_row["used_count"] >= key_row["max_uses"]):
                raise DomainError(400, "Mã mời không hợp lệ hoặc đã hết lượt")
            if key_row["expires_at"] is not None and key_row["expires_at"] <= now:
                raise DomainError(400, "Mã mời đã hết hạn")
            if await accounts.username_exists(conn, username):
                raise DomainError(409, "Tên đăng nhập đã được sử dụng")
            if await accounts.email_exists(conn, email):
                raise DomainError(409, "Email đã được đăng ký")
            if not await keys.consume(conn, key, now):
                raise DomainError(400, "Mã mời đã hết lượt sử dụng")
            try:
                account = await accounts.create(
                    conn, account_id=uuid.uuid4().hex, username=username, email=email,
                    password_hash=pw_hash, role=key_row["role"], invite_key=key,
                    verify_token_hash=hash_token(verify_token), now=now)
            except asyncpg.UniqueViolationError as exc:
                # Đua với một đăng ký khác: transaction rollback nên lượt dùng key được hoàn lại.
                if "email" in (exc.constraint_name or ""):
                    raise DomainError(409, "Email đã được đăng ký") from None
                raise DomainError(409, "Tên đăng nhập đã được sử dụng") from None
            await audit.record(conn, account_id=account["id"], event_type="register", now=now,
                               target_type="account", target_id=account["id"],
                               payload={"role": account["role"]})

        await self._safe_send(self.mailer.send_verify, account["email"], verify_token, account["username"])
        return {"ok": True, "email": account["email"],
                "message": "Đã gửi email xác thực. Kiểm tra hộp thư để kích hoạt tài khoản."}

    # ── xác thực email ──────────────────────────────────────────────────────────

    async def verify_email(self, token: str) -> dict:
        async with self.db.transaction() as conn:
            account = await accounts.get_by_verify_hash(conn, hash_token(token))
            if not account:
                raise DomainError(400, "Link xác thực không hợp lệ hoặc đã được sử dụng")
            issued = account["verify_token_at"]
            if issued is None or self.now() - issued > VERIFY_TOKEN_TTL:
                raise DomainError(400, "Link xác thực đã hết hạn. Đăng nhập và yêu cầu gửi lại.")
            await accounts.mark_verified(conn, account["id"])
            await audit.record(conn, account_id=account["id"], event_type="email_verified",
                               now=self.now(), target_type="account", target_id=account["id"])
        return {"ok": True, "message": "Xác thực email thành công. Đăng nhập ngay."}

    async def resend_verify(self, email: str) -> dict:
        generic = {"ok": True, "message": "Nếu email tồn tại và chưa xác thực, đã gửi lại."}
        async with self.db.connection() as conn:
            account = await accounts.get_by_email(conn, email.strip())
            # KHÔNG tiết lộ email có tồn tại hay không — luôn trả cùng một câu trả lời
            if not account or account["email_verified"] or account["status"] == "suspended":
                return generic
            token = new_token()
            await accounts.set_verify_token(conn, account["id"], hash_token(token), self.now())
        await self._safe_send(self.mailer.send_verify, account["email"], token, account["username"])
        return generic

    # ── đăng nhập / refresh / đăng xuất ────────────────────────────────────────

    async def login(self, *, username: str, password: str, client_type_header: str) -> dict:
        client_type = _FALLBACK_TOOL if client_type_header == _FALLBACK_TOOL else "web"
        async with self.db.connection() as conn:
            account = await accounts.get_by_username(conn, username.strip())
        # Luôn băm một lần (dùng hash giả khi không có account) để thời gian phản hồi không lộ username.
        stored = account["password_hash"] if account else await _dummy_password_hash()
        matched = await asyncio.to_thread(verify_password, password, stored)
        if not account or not matched:
            await self._sleep(self.settings.login_fail_delay_s)
            raise DomainError(401, "Tên đăng nhập hoặc mật khẩu không đúng")

        if account["status"] == "pending" and not account["email_verified"]:
            raise DomainError(403, "Tài khoản chưa xác thực email. "
                                   "Kiểm tra hộp thư hoặc yêu cầu gửi lại email xác thực.")
        if account["status"] == "suspended":
            raise DomainError(403, "Tài khoản đã bị khoá. Liên hệ quản trị viên.")
        if client_type == _FALLBACK_TOOL and account["role"] not in _FALLBACK_ROLES:
            raise DomainError(403, "Tool B chỉ cho phép tài khoản admin hoặc dash_admin đăng nhập")

        now = self.now()
        refresh_raw = new_token()
        async with self.db.transaction() as conn:
            await refresh_tokens.create(
                conn, token_hash=hash_token(refresh_raw), account_id=account["id"], now=now,
                expires_at=now + timedelta(days=self.settings.refresh_token_ttl_days))
            await accounts.update_last_login(conn, account["id"], now)
            if client_type == _FALLBACK_TOOL:
                await audit.record(conn, account_id=account["id"], event_type="login_fallback_tool",
                                   now=now, target_type="account", target_id=account["id"],
                                   payload={"username": account["username"]})
        return {
            "access_token": self._access_token(account, client_type),
            "refresh_token": refresh_raw,
            "expires_in": self.settings.access_token_ttl_s,
            "user": {"id": account["id"], "username": account["username"],
                     "email": account["email"], "role": account["role"]},
        }

    async def refresh(self, refresh_token: str) -> dict:
        async with self.db.connection() as conn:
            record = await refresh_tokens.get_valid(conn, hash_token(refresh_token), self.now())
            if not record:
                raise DomainError(401, "Refresh token không hợp lệ hoặc đã hết hạn")
            account = await accounts.get_by_id(conn, record["account_id"])
        if not account or account["status"] != "active":
            raise DomainError(401, "Tài khoản không khả dụng")
        return {"access_token": self._access_token(account),
                "expires_in": self.settings.access_token_ttl_s}

    async def logout(self, refresh_token: str) -> dict:
        """Fire-and-forget: luôn trả ok dù token không tồn tại (như legacy)."""
        async with self.db.connection() as conn:
            await refresh_tokens.revoke(conn, hash_token(refresh_token))
        return {"ok": True}

    async def current_account(self, access_token: str) -> dict:
        payload = self.decode_access_token(access_token)
        async with self.db.connection() as conn:
            account = await accounts.get_by_id(conn, payload["sub"])
        if not account:
            raise DomainError(401, "Tài khoản không tồn tại")
        if account["status"] == "suspended":
            raise DomainError(403, "Tài khoản đã bị khoá. Liên hệ quản trị viên.")
        return account

    @staticmethod
    def me(account: dict) -> dict:
        return {"id": account["id"], "username": account["username"], "email": account["email"],
                "role": account["role"], "created_at": iso(account["created_at"])}

    # ── quên / đặt lại / đổi mật khẩu ──────────────────────────────────────────

    async def forgot_password(self, email: str) -> dict:
        async with self.db.connection() as conn:
            account = await accounts.get_by_email(conn, email.strip())
            token = None
            if account and account["status"] == "active":  # KHÔNG xác nhận email có tồn tại
                token = new_token()
                await accounts.set_reset_token(conn, account["id"], hash_token(token), self.now())
        if token:
            await self._safe_send(self.mailer.send_reset, account["email"], token, account["username"])
        return {"ok": True, "message": "Nếu email tồn tại, đã gửi link đặt lại mật khẩu."}

    async def reset_password(self, token: str, new_password: str) -> dict:
        pw_hash = await asyncio.to_thread(hash_password, new_password)
        async with self.db.transaction() as conn:
            account = await accounts.get_by_reset_hash(conn, hash_token(token))
            if not account:
                raise DomainError(400, "Link đặt lại mật khẩu không hợp lệ hoặc đã được sử dụng")
            issued = account["reset_token_at"]
            if issued is None or self.now() - issued > RESET_TOKEN_TTL:
                raise DomainError(400, "Link đặt lại mật khẩu đã hết hạn. Yêu cầu gửi lại.")
            await accounts.update_password(conn, account["id"], pw_hash)
            await accounts.clear_reset_token(conn, account["id"])
            await refresh_tokens.revoke_all(conn, account["id"])  # bắt đăng nhập lại ở mọi nơi
            await audit.record(conn, account_id=account["id"], event_type="password_reset",
                               now=self.now(), target_type="account", target_id=account["id"])
        return {"ok": True, "message": "Mật khẩu đã đặt lại. Đăng nhập với mật khẩu mới."}

    async def send_change_code(self, account: dict) -> dict:
        code = new_numeric_code()
        async with self.db.connection() as conn:
            await accounts.set_change_code(conn, account["id"], hash_token(code), self.now())
        await self._safe_send(self.mailer.send_change_code, account["email"], code, account["username"])
        return {"ok": True, "message": "Đã gửi mã xác thực về email của bạn."}

    async def change_password(self, account: dict, code: str, new_password: str) -> dict:
        pw_hash = await asyncio.to_thread(hash_password, new_password)
        code_hash = hash_token(code)
        # Pha KIỂM TRA chạy ngoài transaction: bộ đếm lần thử và việc huỷ mã phải được ghi nhận ngay cả khi
        # sau đó ném lỗi. (Đặt trong transaction thì lỗi làm rollback, bộ đếm không bao giờ tăng.)
        async with self.db.connection() as conn:
            fresh = await accounts.get_by_id(conn, account["id"])
            if not fresh or not fresh["change_code_hash"]:
                raise DomainError(400, "Chưa yêu cầu mã xác thực. Gửi mã trước.")
            issued = fresh["change_code_at"]
            if issued is None or self.now() - issued > CHANGE_CODE_TTL:
                await accounts.clear_change_code(conn, account["id"])
                raise DomainError(400, "Mã xác thực đã hết hạn. Yêu cầu gửi lại.")
            if not hmac.compare_digest(code_hash, fresh["change_code_hash"]):
                attempts = await accounts.bump_change_attempts(conn, account["id"])
                if attempts >= MAX_CHANGE_CODE_ATTEMPTS:
                    await accounts.clear_change_code(conn, account["id"])
                    raise DomainError(400, "Quá nhiều lần nhập sai. Yêu cầu gửi lại mã.")
                raise DomainError(400, "Mã xác thực không đúng")
        # Pha THÀNH CÔNG atomic: dùng mã, đổi mật khẩu, thu hồi phiên, ghi audit — hoặc không gì cả.
        async with self.db.transaction() as conn:
            if not await accounts.consume_change_code(conn, account["id"], code_hash):
                raise DomainError(400, "Chưa yêu cầu mã xác thực. Gửi mã trước.")
            await accounts.update_password(conn, account["id"], pw_hash)
            await refresh_tokens.revoke_all(conn, account["id"])
            await audit.record(conn, account_id=account["id"], event_type="password_changed",
                               now=self.now(), target_type="account", target_id=account["id"])
        return {"ok": True, "message": "Mật khẩu đã thay đổi. Đăng nhập lại với mật khẩu mới."}

    # ── hồ sơ ───────────────────────────────────────────────────────────────────

    async def update_profile(self, account: dict, *, username: str | None, email: str | None) -> dict:
        updates: dict[str, str] = {}
        async with self.db.transaction() as conn:
            if username and username != account["username"]:
                if await accounts.username_exists(conn, username):
                    raise DomainError(409, "Tên đăng nhập đã được sử dụng")
                updates["username"] = username
            if email and email.lower() != account["email"]:
                if await accounts.email_exists(conn, email):
                    raise DomainError(409, "Email đã được đăng ký")
                updates["email"] = email.lower()
            if not updates:
                return {"ok": True, "message": "Không có thay đổi."}
            try:
                await accounts.update_profile(conn, account["id"], updates)
            except asyncpg.UniqueViolationError as exc:
                if "email" in (exc.constraint_name or ""):
                    raise DomainError(409, "Email đã được đăng ký") from None
                raise DomainError(409, "Tên đăng nhập đã được sử dụng") from None
        return {"ok": True, "message": "Đã cập nhật hồ sơ.", "updates": updates}
