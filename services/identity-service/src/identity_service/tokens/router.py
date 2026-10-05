"""JWKS công khai: `GET /.well-known/jwks.json` (docs/api/service-auth-contract-v1.md §3)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from identity_service.deps import get_container

router = APIRouter(tags=["jwks"])


@router.get("/.well-known/jwks.json", summary="JWKS: khoá công khai để verify access token")
async def jwks(request: Request, response: Response) -> dict:
    container = get_container(request)
    response.headers["Cache-Control"] = "public, max-age=300"
    if container.keystore is None:  # chế độ HS256 tương thích: không có khoá công khai để publish
        return {"keys": []}
    return await container.keystore.jwks()
