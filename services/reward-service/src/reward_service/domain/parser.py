"""Parse dữ liệu đầu vào job `username|point` (hoặc `username,point`) — module MỚI (không có ở Node,
CLI Node đọc file trực tiếp trong `run-job.js:parseInputFile`). Logic thuần, không chạm DB/mạng.

Phát hiện: dòng sai định dạng, `point <= 0`, username trùng (theo `normalized_username`) → sinh
`reward_validation_issues` (bảng B.3 mục 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SPLIT = re.compile(r"[|,]")
_MENTION_CHARS = re.compile(r"[<@!>]")


def normalize_username(raw: str) -> str:
    """Bỏ tiền tố `@` và markup mention Discord (`<@!123>`), hạ thường — dùng cho so khớp/khoá."""
    s = raw.strip()
    if s.startswith("@"):
        s = s[1:]
    s = _MENTION_CHARS.sub("", s)
    return s.lower()


@dataclass(frozen=True)
class ParsedRow:
    row_index: int
    raw_username: str
    normalized_username: str
    point: int | None
    valid: bool


@dataclass(frozen=True)
class ValidationIssue:
    row_index: int | None
    severity: str  # 'error' | 'warning'
    code: str
    message: str


def parse_input(content: str) -> list[ParsedRow]:
    """Parse mỗi dòng `username|point` hoặc `username,point`; bỏ dòng rỗng và dòng bắt đầu `#`."""
    rows: list[ParsedRow] = []
    row_index = 0
    for line in content.splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        parts = [p.strip() for p in _SPLIT.split(t)]
        raw_username = parts[0] if parts else ""
        point_raw = parts[1] if len(parts) > 1 else None
        point: int | None = None
        valid = False
        if raw_username and point_raw not in (None, ""):
            try:
                point = int(point_raw)
                valid = point > 0
            except ValueError:
                point = None
                valid = False
        rows.append(ParsedRow(row_index, raw_username, normalize_username(raw_username), point, valid))
        row_index += 1
    return rows


def validate_rows(rows: list[ParsedRow]) -> list[ValidationIssue]:
    """Sinh danh sách `reward_validation_issues`: định dạng sai, `point<=0`, username trùng, rỗng toàn bộ."""
    issues: list[ValidationIssue] = []
    seen: dict[str, int] = {}
    for row in rows:
        if not row.valid:
            issues.append(ValidationIssue(
                row.row_index, "error", "INVALID_ROW",
                f'Dòng {row.row_index}: định dạng không hợp lệ ("{row.raw_username}")'))
            continue
        prior = seen.get(row.normalized_username)
        if prior is not None:
            issues.append(ValidationIssue(
                row.row_index, "warning", "DUPLICATE_USERNAME",
                f'Dòng {row.row_index}: username trùng với dòng {prior} ("{row.raw_username}")'))
        else:
            seen[row.normalized_username] = row.row_index
    if not rows:
        issues.append(ValidationIssue(None, "error", "EMPTY_INPUT", "Không có dòng dữ liệu hợp lệ nào"))
    return issues
