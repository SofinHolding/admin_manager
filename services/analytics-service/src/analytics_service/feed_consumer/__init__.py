"""module: feed_consumer
service: analytics-service
phase: 3
status: planned
responsibility: kéo change feed, idempotent theo event id, replay từ cursor
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).
"""
