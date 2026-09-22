"""Mã hoá/giải mã token Discord bằng Fernet — dịch nghĩa từ plan C.2/D.1.

`REWARD_TOKEN_KEY` là khoá Fernet 32-byte urlsafe-base64 (đã validate định dạng ở `settings.py`).
Token Discord plaintext CHỈ được giải mã ở hai nơi: `api/credentials.py:verify` và
`engine/worker_manager.py` lúc worker khởi động job — KHÔNG BAO GIỜ log, không `repr`, không trả API.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class TokenCrypto:
    """Bọc `cryptography.fernet.Fernet` cho một khoá `REWARD_TOKEN_KEY` cố định."""

    def __init__(self, reward_token_key: str) -> None:
        self._fernet = Fernet(reward_token_key.encode("utf-8"))

    def encrypt(self, plaintext_token: str) -> str:
        return self._fernet.encrypt(plaintext_token.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Không thể giải mã token — ciphertext hỏng hoặc sai khoá") from exc
