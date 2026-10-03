"""Giá trị mặc định dùng chung cho cấu hình xác nhận reply."""

# Khớp câu trả lời thật của Axolink Management ("N XP has been given to @user") — đã hiệu chuẩn từ dữ
# liệu Discord thật. Dùng khi cấu hình/job không có success_pattern (rỗng ⇒ MỌI item sẽ là `unknown`).
DEFAULT_SUCCESS_PATTERN = r"\d+\s*XP has been given to"
