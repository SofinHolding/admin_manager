"""Link trong email phải khớp route của manager-web (`/verify-email`, `/reset-password`), không có tiền tố `/admin` của web legacy."""

from __future__ import annotations

import re
from dataclasses import replace

from identity_service.notifications.adapters import SmtpMailer


async def _links(settings, monkeypatch) -> dict[str, str]:
    sent: dict[str, str] = {}

    async def capture(self, to, subject, content):
        sent[subject] = content

    monkeypatch.setattr(SmtpMailer, "_send", capture)
    mailer = SmtpMailer(replace(settings, base_url="https://web.example", smtp_host="smtp.example", smtp_user="u", smtp_pass="p"))
    await mailer.send_verify("a@example.com", "TOK-VERIFY", "nguoi_dung")
    await mailer.send_reset("a@example.com", "TOK-RESET", "nguoi_dung")
    return {subject: re.search(r'href="([^"]+)"', html).group(1) for subject, html in sent.items()}


async def test_verify_and_reset_links_use_web_routes_on_the_public_base_url(settings, monkeypatch):
    links = await _links(settings, monkeypatch)
    assert links["Xác thực tài khoản"] == "https://web.example/verify-email?token=TOK-VERIFY"
    assert links["Đặt lại mật khẩu"] == "https://web.example/reset-password?token=TOK-RESET"
