"""module: outbox
service: identity-service
phase: 1.4
status: active
responsibility: transactional outbox và phát event account.*
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).
"""
