# Integration tests

Chạy các service thành **tiến trình OS riêng** và kiểm chứng cách ly lỗi.

- `test_process_isolation.py`: dừng/khởi động lại một service, các service khác không đổi PID và vẫn `ready`.
  Không cần Postgres/Redis (chassis không phụ thuộc hạ tầng ngoài ở bước scaffold).
- Phase sau: thêm test với Postgres/Redis thật (cô lập DB chéo, cache-aside, outbox → feed → consumer).
  Các test cần hạ tầng đánh dấu `requires_postgres` và luôn báo SKIP có lý do khi thiếu hạ tầng.

Chạy: `python -m pytest -q` trong thư mục này (hoặc `python infra/scripts/test_all.py`).
