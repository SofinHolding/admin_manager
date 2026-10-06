-- =====================================================================
-- 003_reward_reconcile.sql — đối soát item `unknown` với lịch sử kênh Discord
-- `reconcile_result` = kết luận của lần đối soát gần nhất ('no_reply' = quét hết kênh từ lúc gửi, quá thời gian
-- chờ mà KHÔNG có reply khớp ⇒ an toàn để cho phép gửi lại). Không đụng bảng nào của admin_manager.
-- =====================================================================

ALTER TABLE reward_items ADD COLUMN IF NOT EXISTS reconcile_result text;
ALTER TABLE reward_items ADD COLUMN IF NOT EXISTS reconciled_at timestamptz;

INSERT INTO reward_schema_migrations (version) VALUES ('003_reward_reconcile')
ON CONFLICT (version) DO NOTHING;
