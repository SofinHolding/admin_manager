"""Body request của endpoint auth. Ràng buộc GIỮ NGUYÊN legacy để frontend cũ không bị ảnh hưởng."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class ValidateKeyBody(BaseModel):
    key: str


class CheckUsernameBody(BaseModel):
    username: str


class CheckEmailBody(BaseModel):
    email: str


class RegisterBody(BaseModel):
    key: str
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=8)
    email: EmailStr


class LoginBody(BaseModel):
    username: str
    password: str


class RefreshBody(BaseModel):
    refresh_token: str


class LogoutBody(BaseModel):
    refresh_token: str


class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class ResendVerifyBody(BaseModel):
    email: EmailStr


class ChangePasswordBody(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8)


class UpdateProfileBody(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
