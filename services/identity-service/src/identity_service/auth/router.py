"""Endpoint `/v1/auth/*` — tương thích contract `legacy` của admin-server (cùng đường dẫn, body, thông điệp)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request

from identity_service.auth.schemas import (
    ChangePasswordBody,
    CheckEmailBody,
    CheckUsernameBody,
    ForgotPasswordBody,
    LoginBody,
    LogoutBody,
    RefreshBody,
    RegisterBody,
    ResendVerifyBody,
    ResetPasswordBody,
    UpdateProfileBody,
    ValidateKeyBody,
)
from identity_service.deps import client_ip, current_account, get_container

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/validate-key", summary="Kiểm tra invite key hợp lệ không")
async def validate_key(body: ValidateKeyBody, request: Request) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "validate-key", 10, 60)
    return await c.auth.validate_key(body.key)


@router.post("/check-username", summary="Username đã có ai dùng chưa")
async def check_username(body: CheckUsernameBody, request: Request) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "check", 30, 60)
    return await c.auth.username_available(body.username)


@router.post("/check-email", summary="Email đã đăng ký chưa")
async def check_email(body: CheckEmailBody, request: Request) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "check", 30, 60)
    return await c.auth.email_available(body.email)


@router.post("/register", summary="Đăng ký tài khoản mới bằng invite key")
async def register(body: RegisterBody, request: Request) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "register", 3, 3600)
    return await c.auth.register(key=body.key, username=body.username, password=body.password,
                                 email=body.email)


@router.get("/verify-email", summary="Xác thực email (click link từ email)")
async def verify_email(request: Request, token: str = Query(...)) -> dict:
    return await get_container(request).auth.verify_email(token)


@router.post("/resend-verify", summary="Gửi lại email xác thực")
async def resend_verify(body: ResendVerifyBody, request: Request) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "resend", 5, 3600)
    return await c.auth.resend_verify(body.email)


@router.post("/login", summary="Đăng nhập bằng username + password")
async def login(body: LoginBody, request: Request,
                x_client_type: str = Header("web", alias="X-Client-Type")) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "login", 5, 300)
    return await c.auth.login(username=body.username, password=body.password,
                              client_type_header=x_client_type)


@router.post("/refresh", summary="Đổi refresh token lấy access token mới")
async def refresh(body: RefreshBody, request: Request) -> dict:
    return await get_container(request).auth.refresh(body.refresh_token)


@router.post("/logout", summary="Đăng xuất — thu hồi refresh token")
async def logout(body: LogoutBody, request: Request) -> dict:
    return await get_container(request).auth.logout(body.refresh_token)


@router.get("/me", summary="Thông tin người dùng hiện tại")
async def me(account: dict = Depends(current_account)) -> dict:
    from identity_service.auth.service import AuthService
    return AuthService.me(account)


@router.post("/forgot-password", summary="Gửi email đặt lại mật khẩu")
async def forgot_password(body: ForgotPasswordBody, request: Request) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "forgot", 3, 3600)
    return await c.auth.forgot_password(body.email)


@router.post("/reset-password", summary="Đặt lại mật khẩu bằng token từ email")
async def reset_password(body: ResetPasswordBody, request: Request) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "reset", 10, 3600)  # legacy không giới hạn endpoint này
    return await c.auth.reset_password(body.token, body.new_password)


@router.post("/change-password/send-code", summary="Gửi mã xác thực 6 số về email — bước 1 đổi mật khẩu")
async def send_change_code(request: Request, account: dict = Depends(current_account)) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "change-code", 3, 600)
    return await c.auth.send_change_code(account)


@router.post("/change-password", summary="Xác thực mã 6 số + đặt mật khẩu mới — bước 2")
async def change_password(body: ChangePasswordBody, request: Request,
                          account: dict = Depends(current_account)) -> dict:
    c = get_container(request)
    c.limiter.check(client_ip(request), "change-pw", 5, 600)
    return await c.auth.change_password(account, body.code, body.new_password)


@router.patch("/profile", summary="User tự cập nhật hồ sơ (email, username)")
async def update_profile(body: UpdateProfileBody, request: Request,
                         account: dict = Depends(current_account)) -> dict:
    return await get_container(request).auth.update_profile(account, username=body.username,
                                                            email=body.email)
