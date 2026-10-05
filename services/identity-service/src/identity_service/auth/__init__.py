"""module: auth
service: identity-service
phase: 1.2
status: active
responsibility: register, login, logout, refresh, verify-email, forgot/reset/change password (tương thích contract legacy)
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).
"""
