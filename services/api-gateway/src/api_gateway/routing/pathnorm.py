"""Chuẩn hoá đường dẫn TRƯỚC khi định tuyến và chặn `/internal` mọi biến thể.

Hai việc tách bạch nhưng dùng chung một đầu vào:
1. KIỂM TRA `internal`: giải mã phần trăm lặp đến khi ổn định (chống `%2569nternal`), đổi `\\`→`/`, NFKC + casefold,
   bỏ tham số `;x=y`, giải `.`/`..`. Chỉ cần MỘT segment bằng `internal` (trước hoặc sau khi giải dot-segment)
   là bị chặn (BlockedPath ⇒ 404).
2. ĐƯỜNG DẪN CHUYỂN TIẾP: giải mã đúng MỘT lần, gộp `//`, giải dot-segment, mã hoá lại. Gateway định tuyến
   và chuyển tiếp CÙNG một chuỗi này, nên không có khác biệt phân tích giữa gateway và upstream.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote, unquote_to_bytes

_ENC_SLASH = re.compile(rb"%(?:2f|5c)", re.IGNORECASE)
_PCT = re.compile(rb"%[0-9A-Fa-f]{2}")
_SAFE = "/:@!$&'()*+,;=-._~"
_MAX_DECODE_ROUNDS = 6


class BlockedPath(Exception):
    """Đường dẫn trỏ vào vùng nội bộ ⇒ trả 404 như thể không tồn tại."""


class InvalidPath(Exception):
    """Đường dẫn sai dạng (byte điều khiển, mã hoá hỏng, ...) ⇒ 400."""


@dataclass(frozen=True)
class NormalizedPath:
    match: str       # đường dẫn đã giải mã 1 lần + chuẩn hoá (dùng để định tuyến/rate-limit/log)
    forward: bytes   # cùng đường dẫn, mã hoá lại an toàn để gửi upstream


def _segments_have_internal(text: str) -> bool:
    text = unicodedata.normalize("NFKC", text.replace("\\", "/")).casefold()
    raw = []
    for seg in text.split("/"):
        seg = seg.split(";", 1)[0].rstrip(". \t")
        raw.append(seg)
    if "internal" in raw:
        return True
    stack: list[str] = []
    for seg in raw:
        if seg in ("", "."):
            continue
        if seg == "..":
            if stack:
                stack.pop()
            continue
        stack.append(seg)
    return "internal" in stack


def _deep_decode(raw: bytes) -> str:
    cur = raw
    for _ in range(_MAX_DECODE_ROUNDS):
        nxt = unquote_to_bytes(cur)
        if nxt == cur:
            break
        cur = nxt
    else:
        if _PCT.search(cur):
            raise InvalidPath("too many encoding layers")
    return cur.decode("utf-8", errors="replace")


def normalize_path(raw_path: bytes) -> NormalizedPath:
    if not raw_path.startswith(b"/"):
        raise InvalidPath("path must start with /")
    if any(b < 0x21 or b > 0x7E for b in raw_path):
        # Chặn internal TRƯỚC khi báo lỗi định dạng để biến thể lạ vẫn nhận 404 nhất quán.
        try:
            if _segments_have_internal(_deep_decode(raw_path)):
                raise BlockedPath
        except InvalidPath:
            pass
        raise InvalidPath("control or non-ascii bytes")

    if _segments_have_internal(_deep_decode(raw_path)):
        raise BlockedPath

    if _ENC_SLASH.search(raw_path):
        raise InvalidPath("encoded slash")
    once = unquote_to_bytes(raw_path)
    if b"\x00" in once or b"\\" in once or any(b < 0x20 or b == 0x7F for b in once):
        raise InvalidPath("forbidden character")
    try:
        text = once.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidPath("invalid utf-8") from exc

    parts = text.split("/")
    trailing = parts[-1] in ("", ".", "..")
    stack: list[str] = []
    for seg in parts:
        if seg in ("", "."):
            continue
        if seg == "..":
            if stack:
                stack.pop()
            continue
        stack.append(seg)
    match = "/" + "/".join(stack)
    if trailing and stack:
        match += "/"
    if _segments_have_internal(match):  # phòng thủ lần cuối trên đúng chuỗi sẽ được chuyển tiếp
        raise BlockedPath
    return NormalizedPath(match=match, forward=quote(match, safe=_SAFE).encode("ascii"))
