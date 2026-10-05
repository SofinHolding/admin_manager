"""`/health/live` và `/health/ready` — chuẩn chung cho mọi service.

QUY TẮC: `ready` CHỈ kiểm tài nguyên của CHÍNH service (DB riêng của nó). KHÔNG BAO GIỜ gọi service khác
hay Redis — nếu không, một service chết sẽ kéo các service khác thành "not ready" (distributed monolith).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

ReadinessCheck = Callable[[], Awaitable[bool]]


def make_health_router(service_name: str, service_version: str, contract_version: str,
                       checks: dict[str, ReadinessCheck] | None = None) -> APIRouter:
    router = APIRouter(tags=["health"])
    checks = checks or {}

    @router.get("/health/live", include_in_schema=False)
    async def live() -> dict:
        return {"status": "live", "service": service_name}

    @router.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        results: dict[str, bool] = {}
        for name, check in checks.items():
            try:
                results[name] = bool(await check())
            except Exception:  # noqa: BLE001 — check hỏng = chưa sẵn sàng, không được làm sập endpoint
                results[name] = False
        ok = all(results.values())
        return JSONResponse(status_code=200 if ok else 503,
                            content={"status": "ready" if ok else "not_ready",
                                     "service": service_name, "checks": results})

    @router.get("/health/version", include_in_schema=False)
    async def version() -> dict:
        return {"service": service_name, "version": service_version,
                "contract_version": contract_version}

    return router
