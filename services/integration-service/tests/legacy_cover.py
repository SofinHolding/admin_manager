"""Sổ đăng ký "operation legacy nào đã có test hành vi" — test parity đối chiếu với OpenAPI legacy + route-map.

Dùng: `@covers("GET /v1/resources", "POST /v1/resources")` trên hàm test. Đăng ký xảy ra lúc import (collection),
nên mọi module test đã nạp xong trước khi test parity chạy.
"""

from __future__ import annotations

COVERED: set[str] = set()


def covers(*operations: str):
    def deco(fn):
        COVERED.update(operations)
        return fn
    return deco
