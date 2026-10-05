"""module: country_config
service: analytics-service
phase: 3
status: active
responsibility: alias và tên chuẩn quốc gia
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Bảng alias -> tên quốc gia chuẩn và danh sách tên chuẩn (admin/dash_admin). Áp dụng ở tầng đọc (không sửa dữ liệu nguồn);
mọi thay đổi tăng seal cache trong cùng transaction.
"""
