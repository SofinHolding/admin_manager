"""Mã hoá/giải mã secret credential "chờ nhận" (reveal) bằng Fernet — cùng kiểu với reward-service/crypto.py.

`INTEGRATION_REVEAL_KEY` là khoá Fernet 32-byte urlsafe-base64 (đã validate định dạng ở `config.py`). Secret
plaintext của credential cấp HỘ (admin cấp cho client cá nhân, hoặc provisioning tự tạo) chỉ được giải mã ở
`credentials/service.py:reveal` — ĐÚNG MỘT LẦN, ngay sau đó xoá ciphertext khỏi DB — KHÔNG BAO GIỜ log, không
`repr`, không trả API ở bất kỳ endpoint nào khác.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class RevealCrypto:
    """Bọc `cryptography.fernet.Fernet` cho một khoá `INTEGRATION_REVEAL_KEY` cố định."""

    def __init__(self, reveal_key: str) -> None:
        self._fernet = Fernet(reveal_key.encode("utf-8"))

    def encrypt(self, plaintext_api_key: str) -> str:
        return self._fernet.encrypt(plaintext_api_key.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Không thể giải mã reveal ciphertext — hỏng hoặc sai khoá") from exc

    @staticmethod
    def valid_key_format(value: str) -> bool:
        try:
            Fernet(value.encode("utf-8"))
        except Exception:  # noqa: BLE001
            return False
        return True
