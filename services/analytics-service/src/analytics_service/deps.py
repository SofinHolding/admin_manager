"""Xác thực user (token RS256 verify OFFLINE bằng JWKS) — dùng SDK dùng chung, không tự cài verify."""

from __future__ import annotations

from fastapi import Depends, Request
from manager_sdk import Principal, make_dependencies

from analytics_service.container import Container, get_container

_deps = make_dependencies(lambda request: request.app.state.container.verifier)

require_user = _deps.require_user
require_admin = _deps.require_admin


def container(request: Request) -> Container:
    return get_container(request)


User = Depends(require_user)
Admin = Depends(require_admin)
__all__ = ["Admin", "Principal", "User", "container", "require_admin", "require_user"]
