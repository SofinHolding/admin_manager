"""module: breakdown
service: analytics-service
phase: 3
status: active
responsibility: theo nền tảng, cá nhân, quốc gia, kênh/page, nhóm kênh
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

`channel-breakdown` (nền tảng × chủ đề × quốc gia; managed đếm theo cặp (kênh, chủ đề)) và `compare/{dimension}` với
dimension ∈ platform|owner|country|channel|group|project. Tài khoản đã xoá (`account.deleted`) bị ẩn khỏi so sánh người.
"""
