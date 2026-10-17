"""module: country_comparison
service: analytics-service
phase: 3
status: active
responsibility: so sánh quốc gia (biểu đồ riêng)
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Biểu đồ so sánh quốc gia KHÔNG bị thu hẹp theo bộ lọc `country` (để so sánh các nước với nhau); mọi bộ lọc khác
vẫn áp dụng. Quốc gia hiệu lực: override nhóm > resource; "Khác" khi trống.
"""
