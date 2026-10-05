"""module: filters
service: analytics-service
phase: 3
status: active
responsibility: FilterContext duy nhất cho mọi KPI và biểu đồ
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

MỘT hàm duy nhất (`build_filter_context`) dựng bộ lọc từ query + principal; MỘT dependency khai báo query
(`FilterQuery`) dùng cho mọi endpoint dữ liệu nên KPI và biểu đồ luôn cùng bộ lọc. Người dùng thường bị ÉP về
chính mình ở đây (và chỉ ở đây).
"""

from analytics_service.filters.context import FilterContext, FilterQuery, build_filter_context

__all__ = ["FilterContext", "FilterQuery", "build_filter_context"]
