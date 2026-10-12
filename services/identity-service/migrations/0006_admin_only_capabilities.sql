-- Migration 0006: Đánh dấu capability mạnh là admin_only
-- api_write (ghi snapshot/metrics qua credential) và multi_tool (tạo nhiều integration client)
-- chỉ admin thật mới được gán vào invite_key — dash_admin bị chặn 403 ở service.py.
-- Idempotent: chạy lại an toàn (UPDATE WHERE trùng tên → không lỗi).

UPDATE permissions
   SET admin_only = true
 WHERE name IN ('capability:api_write', 'capability:multi_tool');
