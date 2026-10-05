"""module: traffic
service: analytics-service
phase: 3
status: active
responsibility: chuỗi traffic theo chỉ số
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Port RIÊNG từ `stats_core.traffic_metric`/`top_channels`: 4 chỉ số (views/interactions/comments/videos) theo ngày, `None`
cho ngày chưa có dữ liệu (khác 0), top kênh theo tổng lượt xem. Cùng FilterContext với KPI.
"""
