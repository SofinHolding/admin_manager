"""Bộ kiểm tra view/like/comment/share theo nền tảng (TikTok, YouTube, Facebook) — port từ CheckviewV2, chạy ngay trong tool.

Mọi hàm `check_*` trả cùng một hình dạng (xem `common.py`) và KHÔNG ném lỗi mạng ra ngoài: lỗi thành `{"ok": False, "error": ...}`.
"""
