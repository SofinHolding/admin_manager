"""Entry point: `uvicorn identity_service.main:app`."""

from manager_observability import configure_logging

from identity_service import SERVICE_NAME, __version__
from identity_service.app import create_app
from identity_service import logredact
from identity_service.config import load_settings

_settings = load_settings()
configure_logging(SERVICE_NAME, __version__, _settings.log_level)
logredact.install()
app = create_app(_settings)
