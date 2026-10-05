"""CLI: `python -m identity_service.oidc_clients seed-dev` — tạo/cập nhật client first-party `manager-web` (public).

Redirect URI lấy từ `IDENTITY_WEB_REDIRECT_URIS` (CSV; mặc định chỉ `http://127.0.0.1:5174/admin/callback`),
post-logout từ `IDENTITY_WEB_POST_LOGOUT_URIS`. Không bao giờ tạo secret (đây là public client dùng PKCE).
"""

from __future__ import annotations

import asyncio
import sys


def main(argv: list[str] | None = None) -> int:
    from identity_service.config import ConfigError, load_settings, require_serving_config
    from identity_service.db import Database
    from identity_service.errors import DomainError
    from identity_service.oidc.clients import ClientService

    args = argv if argv is not None else sys.argv[1:]
    if args != ["seed-dev"]:
        print("Dùng: python -m identity_service.oidc_clients seed-dev", file=sys.stderr)
        return 2
    try:
        settings = load_settings()
        require_serving_config(settings)
    except ConfigError as exc:
        print(f"Cấu hình sai: {exc}", file=sys.stderr)
        return 2
    if settings.env == "production":
        print("seed-dev bị từ chối ở production (đăng ký client qua Admin API).", file=sys.stderr)
        return 2

    async def run() -> int:
        db = Database(settings.database_url)
        try:
            client, created = await ClientService(db, settings.env).seed_web(
                list(settings.web_redirect_uris), list(settings.web_post_logout_uris))
        except DomainError as exc:
            print(f"Cấu hình client không hợp lệ: {exc.detail}", file=sys.stderr)
            return 2
        finally:
            await db.close()
        print(("Đã tạo" if created else "Đã cập nhật") + f" client {client['client_id']}: "
              + ", ".join(client["redirect_uris"]))
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
