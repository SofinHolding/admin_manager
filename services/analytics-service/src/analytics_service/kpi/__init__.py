"""module: kpi
service: analytics-service
phase: 3
status: active
responsibility: summary: tổng lượt xem, video đăng, kênh quản lý/hoạt động
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Port RIÊNG từ công thức `stats_core.summary` của legacy (không import legacy). Lượt xem kỳ trước = kỳ liền kề cùng số ngày.
`videos_posted` = số event `post_ok`. Kênh quản lý/hoạt động theo cờ managed/active của resource (cặp (kênh, chủ đề)).
"""
