"""Cổng gửi thông báo. Nghiệp vụ chỉ biết interface này, không biết SMTP.

Tách sẵn để về sau chuyển thành Notification Service độc lập mà không sửa nghiệp vụ Identity.
"""

from __future__ import annotations

from typing import Protocol


class Mailer(Protocol):
    async def send_verify(self, email: str, token: str, username: str) -> None: ...

    async def send_reset(self, email: str, token: str, username: str) -> None: ...

    async def send_change_code(self, email: str, code: str, username: str) -> None: ...
