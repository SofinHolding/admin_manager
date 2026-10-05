"""Entry point: `uvicorn analytics_service.main:app`."""

from manager_observability import configure_logging

from analytics_service import SERVICE_NAME, __version__
from analytics_service.app import create_app
from analytics_service.config import load_settings

_settings = load_settings()
configure_logging(SERVICE_NAME, __version__, _settings.log_level)
app = create_app()
