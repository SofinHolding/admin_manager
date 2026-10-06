-- integration_db: `account_projection.status_seq` = seq của event Identity cuối cùng đã đặt `status`.
-- Replay dead-letter áp lại event CŨ; so với cột này để không ghi đè trạng thái mới hơn (suspended -> active ... theo thứ tự thật).
-- Forward-only. Không sửa 0001-0003.
ALTER TABLE account_projection ADD COLUMN IF NOT EXISTS status_seq bigint NOT NULL DEFAULT 0;
