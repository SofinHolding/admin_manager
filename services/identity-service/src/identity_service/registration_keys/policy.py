"""Chuẩn hoá các trường phụ của invite key: `allowed_apps` (tự cấp permission `<app>:access`).

Thuần và CỤC BỘ (không gọi service khác). `capability_grant`/`role` của key giờ do preset
(`registration_keys/presets.py`) quyết định — xem `service.py`.
"""

from __future__ import annotations

import re

from identity_service.errors import DomainError

_APP = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_LIST = 32


def _err(status: int, message: str, code: str) -> DomainError:
    return DomainError(status, message, code=code)


def _str_list(value: object, pattern: re.Pattern, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST:
        raise _err(422, f"{label} phải là danh sách (tối đa {MAX_LIST})", "REQUEST_VALIDATION_FAILED")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not pattern.match(item):
            raise _err(422, f"{label} chứa giá trị không hợp lệ", "REQUEST_VALIDATION_FAILED")
        if item not in out:
            out.append(item)
    return out


def normalize_apps(raw: object) -> list[str]:
    return _str_list(raw if raw is not None else [], _APP, "allowed_apps")
