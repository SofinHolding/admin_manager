# Migration của integration-service

- Chỉ chứa DDL cho database `integration_db` của CHÍNH service này.
- Tên file: `NNNN_mo_ta.sql`, chạy tăng dần, forward-only. Không sửa file đã áp dụng.
- Mọi bảng tạo ở đây phải có trong `owned_tables` của registry; không tạo bảng của service khác.
- Chạy như một job riêng, không chạy ngầm lúc service khởi động.
