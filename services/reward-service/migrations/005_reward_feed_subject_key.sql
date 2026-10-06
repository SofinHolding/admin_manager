-- 005 — Khóa đặc trưng của event đã xử lý, để replay dead-letter so thứ tự ĐÚNG chủ thể.
-- `subject` của event Identity chỉ là `acc:<account_id>` (không gồm tên permission/sid) nên không đủ so sánh.
-- `subject_key` = `account|permission` (permission_changed), `account|sid` / `account|*` (session.revoked),
-- hoặc `account` (các loại còn lại). Dòng cũ để rỗng = không bao giờ làm event nào bị coi là "đã bị thay thế".
ALTER TABLE processed_events ADD COLUMN IF NOT EXISTS subject_key text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS processed_events_supersede_idx ON processed_events (type, subject_key, seq);

INSERT INTO reward_schema_migrations (version) VALUES ('005_reward_feed_subject_key')
ON CONFLICT (version) DO NOTHING;
