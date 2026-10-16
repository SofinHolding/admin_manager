"""Adapter gửi email: SMTP (TLS bắt buộc) và Log (khi chưa cấu hình SMTP).

Khác legacy: không ghi token ra log ở production khi thiếu SMTP (legacy log token để "đủ cho dev"; ở production
điều đó làm lộ token vào log). Chỉ `env=dev` mới in token.
"""

from __future__ import annotations

import asyncio
import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from identity_service.config import Settings

logger = logging.getLogger("identity.notifications")

_BUTTON = ("background:#2563eb;color:white;padding:12px 32px;border-radius:8px;text-decoration:none;"
           "font-weight:600;display:inline-block;")


def _page(title: str, username: str, intro: str, link: str | None, footer: str, code: str | None = None) -> str:
    u = html.escape(username)
    body = f"<h2>{html.escape(title)}</h2><p>Xin chào <strong>{u}</strong>,</p><p>{html.escape(intro)}</p>"
    if link:
        safe = html.escape(link, quote=True)
        body += (f'<div style="text-align:center;margin:32px 0;"><a href="{safe}" style="{_BUTTON}">'
                 f"{html.escape(title)}</a></div><p style=\"color:#888;font-size:13px;\">"
                 f'Nếu nút không hoạt động, copy link: <a href="{safe}">{safe}</a></p>')
    if code:
        body += (f'<p style="text-align:center;font-size:32px;letter-spacing:8px;font-weight:700;">'
                 f"{html.escape(code)}</p>")
    body += f'<p style="color:#888;font-size:13px;">{html.escape(footer)}</p>'
    return f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;">{body}</div>'


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _send_sync(self, to: str, subject: str, content: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = subject, self._s.smtp_from, to
        msg.attach(MIMEText(content, "html", "utf-8"))
        with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port, timeout=30) as smtp:
            smtp.starttls()  # TLS bắt buộc: không gửi mật khẩu SMTP trần
            smtp.login(self._s.smtp_user, self._s.smtp_pass)
            smtp.sendmail(self._s.smtp_from, [to], msg.as_string())

    async def _send(self, to: str, subject: str, content: str) -> None:
        await asyncio.to_thread(self._send_sync, to, subject, content)

    async def send_verify(self, email: str, token: str, username: str) -> None:
        link = f"{self._s.base_url}/verify-email?token={token}"
        await self._send(email, "Xác thực tài khoản", _page(
            "Xác thực email", username, "Nhấn nút bên dưới để xác thực email và kích hoạt tài khoản.",
            link, "Link có hiệu lực trong 24 giờ. Nếu bạn không yêu cầu, hãy bỏ qua email này."))

    async def send_reset(self, email: str, token: str, username: str) -> None:
        link = f"{self._s.base_url}/reset-password?token={token}"
        await self._send(email, "Đặt lại mật khẩu", _page(
            "Đặt lại mật khẩu", username, "Nhấn nút bên dưới để tạo mật khẩu mới.",
            link, "Link có hiệu lực trong 1 giờ. Nếu bạn không yêu cầu, hãy bỏ qua email này."))

    async def send_change_code(self, email: str, code: str, username: str) -> None:
        await self._send(email, "Mã xác thực đổi mật khẩu", _page(
            "Mã xác thực", username, "Nhập mã dưới đây để đổi mật khẩu.", None,
            "Mã có hiệu lực trong 10 phút. Nếu bạn không yêu cầu, hãy đổi mật khẩu ngay.", code=code))


class LogMailer:
    """Không gửi email thật. `dev` thì in token để thử tay; môi trường khác thì KHÔNG in token."""

    def __init__(self, settings: Settings) -> None:
        self._dev = settings.env == "dev"

    def _log(self, kind: str, email: str, secret: str) -> None:
        if self._dev:
            logger.info("[dev] %s cho %s: %s", kind, email, secret)
        else:
            logger.warning("SMTP chưa cấu hình, không gửi được %s cho %s", kind, email)

    async def send_verify(self, email: str, token: str, username: str) -> None:
        self._log("token xác thực email", email, token)

    async def send_reset(self, email: str, token: str, username: str) -> None:
        self._log("token đặt lại mật khẩu", email, token)

    async def send_change_code(self, email: str, code: str, username: str) -> None:
        self._log("mã đổi mật khẩu", email, code)


def build_mailer(settings: Settings):
    return SmtpMailer(settings) if settings.smtp_configured else LogMailer(settings)
