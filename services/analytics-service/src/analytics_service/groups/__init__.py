"""module: groups
service: analytics-service
phase: 3
status: active
responsibility: nhóm kênh và nhãn nền tảng do viewer quản lý
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Nhóm kênh + thành viên (`channel_ref`, `platform_tag`, `country_override`) CRUD theo CHỦ SỞ HỮU: admin xem được nhóm của
người khác nhưng không sửa hộ. Mọi thay đổi tăng seal cache trong cùng transaction.
"""
