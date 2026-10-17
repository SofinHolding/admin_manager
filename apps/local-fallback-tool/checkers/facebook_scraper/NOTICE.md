# Facebook scraper (vendored)

Nguyên văn từ CheckviewV2 `backend/checker/facebook/` (commit `74026c1`, 2026-10-14) — KHÔNG sửa tay ở đây; cập nhật bằng cách chép lại từ nguồn.
Dùng bởi `checkers/facebook.py` (kiểm tra view/like/comment/share của link bài / reel Facebook bằng cookie).

- `fb_scraper.py` — `FBScraperService.get_reel_by_url` / `get_post_by_url` (HTTP, curl_cffi nếu có).
- `core/settings.py` — đọc cookie từ biến môi trường `FB_COOKIES` (checkers/facebook.py đặt biến này trước mỗi lần gọi, có khoá).
- `core/exceptions/http.py` — `BadRequestException`.
- Các import tuỳ chọn `account_headers` / `proxy_utils` trong `fb_scraper.py` đã có fallback nội bộ (ép `socks5h`).
