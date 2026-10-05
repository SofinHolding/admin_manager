"""module: readmodels
service: analytics-service
phase: 3
status: active
responsibility: dựng và cập nhật read model từ dữ liệu canonical
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Nguồn DUY NHẤT là canonical export đã commit của Integration (qua feed_consumer). Không đọc DB/bảng Integration, không
đọc payload thô. Upsert chỉ ghi đè khi revision đến >= revision đang lưu (replay idempotent, bản cũ không làm số lùi).
Bản ghi `removed` được giữ (lịch sử) nhưng loại khỏi mọi số liệu. Dựng dataset + chọn tập kênh theo FilterContext nằm ở
`dataset.py`; `query.py` là đường chung cho mọi endpoint dữ liệu.
"""
