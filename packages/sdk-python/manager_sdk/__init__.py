"""manager_sdk — SDK dùng chung để verify token Identity (offline qua JWKS), kiểm service key và đọc feed nội bộ.

Chỉ chứa tiện ích GENERIC (không logic nghiệp vụ của service nào). Mọi service dùng SDK này thay vì tự cài verify.
"""

from manager_sdk.errors import AuthError
from manager_sdk.fastapi_deps import install_auth_error_handler, make_dependencies
from manager_sdk.feed_client import FeedClient, FeedError, backoff_delays
from manager_sdk.principal import Principal
from manager_sdk.service_key import ServiceKeyAuth
from manager_sdk.verifier import TokenVerifier

__all__ = ["AuthError", "FeedClient", "FeedError", "Principal", "ServiceKeyAuth", "TokenVerifier",
           "backoff_delays", "install_auth_error_handler", "make_dependencies"]
