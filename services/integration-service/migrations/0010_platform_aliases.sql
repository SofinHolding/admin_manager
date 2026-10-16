-- integration_db 0010: MÃ NỀN TẢNG PHỤ (alias). Một nền tảng (tên hiển thị) có một mã chính (`platform_registry.key`) và nhiều mã phụ.
-- Tool gửi dữ liệu kèm MỘT mã bất kỳ (chính hoặc phụ); server chuẩn hoá về mã chính trước khi lưu, nên Dashboard gom đúng nền tảng.
-- Mã (chính + phụ) là DUY NHẤT toàn hệ thống: không mã phụ nào trùng mã chính hay mã phụ khác (kiểm trong transaction có khoá).
-- Forward-only, idempotent.
CREATE TABLE IF NOT EXISTS platform_aliases (
    alias        text PRIMARY KEY CHECK (alias ~ '^[a-z][a-z0-9_]{0,63}$'),
    platform_key text NOT NULL REFERENCES platform_registry (key) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS platform_aliases_platform_idx ON platform_aliases (platform_key);
