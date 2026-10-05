"""Dựng ứng dụng: chassis chuẩn + (các router của module sẽ được gắn ở đây theo phase)."""

from __future__ import annotations

from fastapi import FastAPI
from manager_observability import create_service_app

from integration_service import SERVICE_NAME, __version__


def create_app() -> FastAPI:
    # Readiness chỉ kiểm tài nguyên CỦA CHÍNH service (DB riêng). Thêm check ở đây khi có kết nối DB.
    return create_service_app(service_name=SERVICE_NAME, service_version=__version__,
                              readiness_checks={})
