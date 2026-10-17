"""Sinh / đóng băng OpenAPI công khai của analytics-service.

    python -m analytics_service.openapi_dump            # in ra stdout
    python -m analytics_service.openapi_dump --write    # ghi docs/api/openapi/analytics-service.v1.json

Bản đóng băng là HỢP ĐỒNG. Thay đổi phá vỡ (xoá/đổi endpoint, field, mã lỗi) = contract version mới, không ghi đè
bản đóng băng.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FROZEN = Path(__file__).resolve().parents[4] / "docs" / "api" / "openapi" / "analytics-service.v1.json"


def build_spec() -> dict:
    from analytics_service.app import create_app
    from analytics_service.config import Settings

    # Cấu hình giả chỉ để dựng app (không kết nối DB/Identity khi sinh tài liệu).
    settings = Settings(port=8432, log_level="INFO", database_url="postgresql://doc:doc@127.0.0.1:1/doc",
                        redis_url="", issuer="http://identity.invalid",
                        identity_jwks_url="http://identity.invalid/.well-known/jwks.json")
    return json.loads(json.dumps(create_app(settings).openapi()))


def render(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = render(build_spec())
    if "--write" in args:
        FROZEN.parent.mkdir(parents=True, exist_ok=True)
        FROZEN.write_text(text, encoding="utf-8", newline="\n")
        print(f"đã ghi {FROZEN}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
