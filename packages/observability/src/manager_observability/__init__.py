"""manager_observability — chassis generic: log JSON, request-id, health, app factory.

Chỉ chứa tiện ích KHÔNG mang nghiệp vụ. Không import code của service nào.
"""

from manager_observability.app_factory import create_service_app
from manager_observability.logging_setup import configure_logging
from manager_observability.request_context import current_request_id

__all__ = ["create_service_app", "configure_logging", "current_request_id"]
