"""Phân loại kết quả một attempt → retry / fail / unknown — dịch từ `src/reward/domain/error-classifier.js`.

Nguyên tắc vàng (không đổi): *chỉ retry khi CHỨNG MINH được request chưa hề rời máy*. Bảng ánh xạ
`httpx` → quyết định đúng D.4 của kế hoạch: `ConnectError`/`ConnectTimeout`/`PoolTimeout` (chưa gửi)
⇒ retry; mọi lỗi khác SAU khi đã bắt đầu gửi (`WriteTimeout`, `ReadTimeout`, `RemoteProtocolError`, …)
⇒ `unknown` (không chứng minh được kết quả) — không bao giờ tự động gửi lại.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# Lỗi CHƯA kết nối / CHƯA gửi được byte nào — an toàn để thử lại (job_runner sẽ claim lại item).
_RETRYABLE_BEFORE_SEND = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass(frozen=True)
class ClassifyResult:
    decision: str  # 'retry' | 'fail' | 'unknown'
    failure_code: str
    pause_job: bool


def classify(*, http_status: int | None = None, exc: BaseException | None = None) -> ClassifyResult:
    """Phân loại theo ngoại lệ `httpx` (đã gửi request) hoặc `http_status` (đã có phản hồi)."""
    if exc is not None:
        if isinstance(exc, _RETRYABLE_BEFORE_SEND):
            return ClassifyResult("retry", "NETWORK", False)
        # WriteTimeout/WriteError/ReadTimeout/ReadError/RemoteProtocolError/mọi lỗi lạ khác:
        # không chứng minh được request có tới nơi hay không → unknown, KHÔNG retry tự động.
        return ClassifyResult("unknown", "SEND_RESULT_UNKNOWN", False)

    if http_status == 429:
        return ClassifyResult("retry", "RATE_LIMITED", False)
    if http_status == 400:
        return ClassifyResult("fail", "INVALID_COMMAND_TEXT", False)
    if http_status == 401:
        return ClassifyResult("fail", "AUTH_INVALID", True)
    if http_status == 403:
        return ClassifyResult("fail", "MISSING_PERMISSION", True)
    if http_status == 404:
        return ClassifyResult("fail", "CHANNEL_NOT_FOUND", True)
    if http_status is not None and http_status >= 500:
        return ClassifyResult("unknown", "UPSTREAM_5XX", False)

    return ClassifyResult("unknown", "SEND_RESULT_UNKNOWN", False)
