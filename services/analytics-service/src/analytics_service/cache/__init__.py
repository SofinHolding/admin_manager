"""module: cache
service: analytics-service
phase: 3
status: planned
responsibility: Cache-Aside với Redis, invalidate sau commit
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).
"""
