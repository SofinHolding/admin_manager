"""Gói các thành phần của service để router lấy qua `request.app.state.container` (không dùng biến global)."""

from __future__ import annotations

from dataclasses import dataclass

from identity_service.accounts.service import AccountAdminService
from identity_service.auth.ratelimit import RateLimiter
from identity_service.auth.service import AuthService
from identity_service.config import Settings
from identity_service.db import Database
from identity_service.permissions.service import PermissionService
from identity_service.registration_keys.service import RegistrationKeyService
from identity_service.tokens.keys import KeyStore
from identity_service.tokens.service import TokenService


@dataclass
class Container:
    settings: Settings
    db: Database
    auth: AuthService
    keys: RegistrationKeyService
    accounts_admin: AccountAdminService
    permissions: PermissionService
    tokens: TokenService
    keystore: KeyStore | None
    limiter: RateLimiter
