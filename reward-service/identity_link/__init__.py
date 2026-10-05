"""identity_link — mọi liên kết của Reward với Identity, DUY NHẤT qua API công khai và feed nội bộ.

module: identity_link
service: reward-service
phase: 4
status: active
responsibility: client gọi API công khai Identity (proxy auth, grant/revoke, danh sách user) với timeout + backoff; consumer kéo feed Identity dựng account_projection/account_denylist và tạm dừng job ngay khi thu quyền
boundary: không import code Identity, không chạm bảng accounts/invite_keys/refresh_tokens; chỉ HTTP + bảng riêng của Reward
"""

import sdk_path  # noqa: F401,E402  — bảo đảm `manager_sdk` import được
