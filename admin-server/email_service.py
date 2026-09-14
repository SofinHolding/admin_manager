"""Gửi email xác thực và reset password qua SMTP.

Nếu thiếu biến môi trường SMTP thì KHÔNG gửi — chỉ log token ra console. Đủ cho dev/test mà không
bắt buộc phải cấu hình SMTP server.

Gmail: dùng App Password (không phải mật khẩu Google thường), bật 2FA trước.
  SMTP_HOST=smtp.gmail.com  SMTP_PORT=587  SMTP_USER=...@gmail.com  SMTP_PASS=xxxx-xxxx-xxxx-xxxx
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("admin.email")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8421")


def _is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _send(to: str, subject: str, html: str) -> None:
    """Gửi một email — đồng bộ, chạy trong thread pool từ caller async.

    TLS bắt buộc: không gửi trần qua mạng. `starttls()` nâng cấp kết nối TCP thường lên TLS
    trước khi gửi mật khẩu SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_FROM, [to], msg.as_string())


async def send_verify_email(email: str, token: str, username: str) -> None:
    """Gửi email xác thực tài khoản. Nếu SMTP chưa cấu hình → chỉ log."""
    if not _is_configured():
        logger.info("SMTP chưa cấu hình. Verify token cho %s: %s", email, token)
        return

    link = f"{BASE_URL}/v1/auth/verify-email?token={token}"
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 480px; margin: 0 auto; padding: 32px 24px;">
        <h2 style="color: #1a1a1a; margin-bottom: 16px;">Xác thực tài khoản</h2>
        <p style="color: #4a4a4a; line-height: 1.6;">
            Xin chào <strong>{username}</strong>,
        </p>
        <p style="color: #4a4a4a; line-height: 1.6;">
            Click vào nút bên dưới để xác thực email và kích hoạt tài khoản:
        </p>
        <div style="text-align: center; margin: 32px 0;">
            <a href="{link}"
               style="background: #2563eb; color: white; padding: 12px 32px; border-radius: 8px;
                      text-decoration: none; font-weight: 600; display: inline-block;">
                Xác thực email
            </a>
        </div>
        <p style="color: #888; font-size: 13px; line-height: 1.5;">
            Nếu nút không hoạt động, copy link sau vào trình duyệt:<br>
            <a href="{link}" style="color: #2563eb; word-break: break-all;">{link}</a>
        </p>
        <p style="color: #888; font-size: 13px;">
            Link có hiệu lực trong 24 giờ. Nếu bạn không yêu cầu, hãy bỏ qua email này.
        </p>
    </div>
    """

    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send, email, "Xác thực tài khoản", html)
    logger.info("Đã gửi email xác thực tới %s", email)


async def send_reset_email(email: str, token: str, username: str) -> None:
    """Gửi email đặt lại mật khẩu. Nếu SMTP chưa cấu hình → chỉ log."""
    if not _is_configured():
        logger.info("SMTP chưa cấu hình. Reset token cho %s: %s", email, token)
        return

    link = f"{BASE_URL}/admin/reset-password?token={token}"
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 480px; margin: 0 auto; padding: 32px 24px;">
        <h2 style="color: #1a1a1a; margin-bottom: 16px;">Đặt lại mật khẩu</h2>
        <p style="color: #4a4a4a; line-height: 1.6;">
            Xin chào <strong>{username}</strong>,
        </p>
        <p style="color: #4a4a4a; line-height: 1.6;">
            Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn.
            Click vào nút bên dưới để tạo mật khẩu mới:
        </p>
        <div style="text-align: center; margin: 32px 0;">
            <a href="{link}"
               style="background: #2563eb; color: white; padding: 12px 32px; border-radius: 8px;
                      text-decoration: none; font-weight: 600; display: inline-block;">
                Đặt lại mật khẩu
            </a>
        </div>
        <p style="color: #888; font-size: 13px; line-height: 1.5;">
            Nếu nút không hoạt động, copy link sau vào trình duyệt:<br>
            <a href="{link}" style="color: #2563eb; word-break: break-all;">{link}</a>
        </p>
        <p style="color: #888; font-size: 13px;">
            Link có hiệu lực trong 1 giờ. Nếu bạn không yêu cầu, hãy bỏ qua email này.
        </p>
    </div>
    """

    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send, email, "Đặt lại mật khẩu", html)
    logger.info("Đã gửi email reset password tới %s", email)
