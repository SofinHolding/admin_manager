"""Cấp / xoay / thu hồi API key máy (mặt phẳng USER). `api_key` plaintext chỉ có trong response cấp/xoay."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from manager_sdk import Principal

from integration_service.api_models import (CredentialCreate, CredentialIssued, CredentialRevoked, Envelope,
                                            RotateRequest)
from integration_service.deps import user_bearer, get_container, require_user, require_tool_integration
from integration_service import examples as ex
from integration_service.http_utils import error_responses, ok, ok_response

router = APIRouter(prefix="/v1/integrations", tags=["credentials"], dependencies=[Depends(user_bearer)])

_ISSUE_DOC = ("Chủ hoặc admin, client `active` (`draft` => 409 `CLIENT_NOT_READY`), email đã xác thực. CHỦ nhận "
             "`api_key` (`mpk_<key_id>.<secret>`) **một lần duy nhất**. Admin cấp HỘ trên client `personal` KHÔNG "
             "thấy `api_key` — response có `reveal_state=\"pending\"`, chủ tự nhận qua `POST /v1/integrations/me/"
             "credential/reveal`. Admin cấp trên client `shared` nhận `api_key` như bình thường. Server chỉ lưu "
             "key_id + sha256(secret). Scope yêu cầu không được vượt `allowed_scopes` của client.")


async def _issue(request: Request, client_id: str, body: CredentialCreate, principal: Principal):
    c = get_container(request)
    async with c.audit.scope(request, "credential.issue", actor_account_id=principal.sub, client_id=client_id,
                             account_id=principal.sub) as extra:
        issued = await c.credentials.issue(principal, client_id, scopes=body.scopes, label=body.label,
                                           expires_in_days=body.expires_in_days)
        extra["key_id"] = issued["key_id"]
    return ok(issued, status=201, headers={"Cache-Control": "no-store"})


@router.post("/clients/{client_id}/credentials", status_code=201, summary="Cấp API key", description=_ISSUE_DOC,
             responses={201: ok_response(Envelope[CredentialIssued], "Key mới (plaintext chỉ lần này)", ex.CREDENTIAL_ISSUED),
                        **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def issue_credential(request: Request, client_id: str, body: CredentialCreate | None = None,
                           principal: Principal = Depends(require_tool_integration)):
    return await _issue(request, client_id, body or CredentialCreate(), principal)


@router.post("/{client_id}/credentials", status_code=201, deprecated=True, include_in_schema=True,
             summary="Cấp API key (alias tương thích tài liệu cũ)", description=_ISSUE_DOC,
             responses={201: ok_response(Envelope[CredentialIssued], "Key mới (plaintext chỉ lần này)", ex.CREDENTIAL_ISSUED),
                        **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def issue_credential_alias(request: Request, client_id: str, body: CredentialCreate | None = None,
                                 principal: Principal = Depends(require_tool_integration)):
    return await _issue(request, client_id, body or CredentialCreate(), principal)


@router.post("/clients/{client_id}/credentials/{key_id}/rotate", status_code=201, summary="Xoay API key",
             description=("Chủ hoặc admin. Cấp key mới cùng scope; key cũ còn hiệu lực `grace_seconds` (mặc định 0 = thu hồi "
                          "ngay và phát `credential.revoked`)."),
             responses={201: ok_response(Envelope[CredentialIssued], "Key mới", ex.CREDENTIAL_ROTATED),
                        **error_responses(401, 403, 404, 409, 422, 429, 503)})
async def rotate_credential(request: Request, client_id: str, key_id: str, body: RotateRequest | None = None,
                            principal: Principal = Depends(require_tool_integration)):
    c = get_container(request)
    async with c.audit.scope(request, "credential.rotate", actor_account_id=principal.sub, client_id=client_id,
                             key_id=key_id):
        issued = await c.credentials.rotate(principal, client_id, key_id,
                                            grace_seconds=(body or RotateRequest()).grace_seconds)
    return ok(issued, status=201, headers={"Cache-Control": "no-store"})


@router.delete("/clients/{client_id}/credentials/{key_id}", summary="Thu hồi API key",
               description="Chủ hoặc admin. Có hiệu lực tức thì: request kế tiếp bằng key này nhận `AUTH_REVOKED`. "
                           "Gọi lại là no-op.",
               responses={200: ok_response(Envelope[CredentialRevoked], "Đã thu hồi", ex.CREDENTIAL_REVOKED),
                          **error_responses(401, 404, 429, 503)})
async def revoke_credential(request: Request, client_id: str, key_id: str,
                            principal: Principal = Depends(require_user)):
    c = get_container(request)
    async with c.audit.scope(request, "credential.revoke", actor_account_id=principal.sub, client_id=client_id,
                             key_id=key_id):
        result = await c.credentials.revoke(principal, client_id, key_id)
    return ok(result)


@router.post("/me/credential/reveal", summary="Nhận plaintext credential cấp hộ",
             description=("Chủ client personal. Credential `reveal_state=pending` chưa hết hạn và client `active` => "
                          "trả plaintext MỘT LẦN rồi xoá ciphertext (`reveal_state=revealed`). Hết hạn => 410 "
                          "`REVEAL_EXPIRED`. Không có pending => 404 `REVEAL_NOT_AVAILABLE`. Email chưa xác thực => "
                          "403 `EMAIL_NOT_VERIFIED`."),
             responses={200: ok_response(Envelope[CredentialIssued], "Credential plaintext", ex.CREDENTIAL_ISSUED),
                        **error_responses(401, 403, 404, 410, 429, 503)})
async def reveal_credential(request: Request, principal: Principal = Depends(require_tool_integration)):
    c = get_container(request)
    async with c.audit.scope(request, "credential.reveal", actor_account_id=principal.sub,
                             account_id=principal.sub) as extra:
        issued = await c.credentials.reveal(principal)
        extra["key_id"] = issued["key_id"]
    return ok(issued, headers={"Cache-Control": "no-store"})
