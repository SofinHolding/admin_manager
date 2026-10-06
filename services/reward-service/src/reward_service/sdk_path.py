"""Đưa `manager_sdk` (packages/sdk-python) vào sys.path khi chưa cài như package.

Thứ tự tìm: biến `REWARD_SDK_PATH` → `<repo>/packages/sdk-python` (monorepo) → `./vendor` (bản sao đi kèm bản deploy).
Import module này TRƯỚC khi import `manager_sdk`. Không có tác dụng phụ nếu SDK đã import được.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def bootstrap_sdk_path() -> None:
    candidates = [os.environ.get("REWARD_SDK_PATH", "").strip(),
                  os.path.join(_HERE, "..", "..", "..", "..", "packages", "sdk-python"), os.path.join(_HERE, "..", "..", "vendor")]
    for cand in candidates:
        if cand and os.path.isdir(os.path.join(cand, "manager_sdk")):
            path = os.path.abspath(cand)
            if path not in sys.path:
                sys.path.append(path)
            return


bootstrap_sdk_path()
