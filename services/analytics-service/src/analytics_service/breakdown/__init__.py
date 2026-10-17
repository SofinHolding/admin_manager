"""module: breakdown
service: analytics-service
phase: 3
status: active
responsibility: theo nền tảng, cá nhân, quốc gia, kênh/page, nhóm kênh
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

`channel-breakdown` (nền tảng × quốc gia; managed đếm theo kênh) và `compare/{dimension}` với
dimension ∈ platform|owner|country|channel|group. Tài khoản đã xoá (`account.deleted`) bị ẩn khỏi so sánh người.
"""
