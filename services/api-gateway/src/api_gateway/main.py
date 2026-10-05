"""Entry point: `uvicorn api_gateway.main:app` hoặc `python -m api_gateway.main`.

Cờ uvicorn BẮT BUỘC (đã đặt trong `run()` và Dockerfile): `proxy_headers=False` (gateway tự quyết định tin
X-Real-IP của proxy nào), `server_header=False` (không lộ phiên bản), `access_log=False` (access log gốc của
uvicorn ghi nguyên query ⇒ lộ token; gateway có access log đã che secret riêng).
"""

from manager_observability import configure_logging

from api_gateway import SERVICE_NAME, __version__
from api_gateway.app import create_app
from api_gateway.config import load_settings

_settings = load_settings()
configure_logging(SERVICE_NAME, __version__, _settings.log_level)
app = create_app(_settings)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=_settings.port, proxy_headers=False, server_header=False,
                access_log=False, log_config=None,
                h11_max_incomplete_event_size=_settings.max_header_bytes)


if __name__ == "__main__":
    run()
