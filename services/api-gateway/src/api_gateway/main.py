"""Entry point: `uvicorn api_gateway.main:app`."""

from manager_observability import configure_logging

from api_gateway import SERVICE_NAME, __version__
from api_gateway.app import create_app
from api_gateway.config import load_settings

_settings = load_settings()
configure_logging(SERVICE_NAME, __version__, _settings.log_level)
app = create_app()
