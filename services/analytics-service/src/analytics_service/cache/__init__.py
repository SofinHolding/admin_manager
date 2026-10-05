"""module: cache
service: analytics-service
phase: 3
status: active
responsibility: Cache-Aside với Redis, invalidate sau commit
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Redis là cache THUẦN và TUỲ CHỌN. Khoá: `analytics:v1:{seal}:{scope}:{endpoint}:{sha256(filter)}`. `seal` đọc từ
PostgreSQL và tăng TRONG CÙNG transaction với mọi thay đổi số liệu nên khoá cũ vô hiệu tức thì (không dựa TTL); khoá nào
cũng có TTL; scope người gọi nằm trong khoá. Redis chết/không cấu hình => đọc thẳng PostgreSQL, không lỗi.
"""

from analytics_service.cache.store import ResultCache, build_key

__all__ = ["ResultCache", "build_key"]
