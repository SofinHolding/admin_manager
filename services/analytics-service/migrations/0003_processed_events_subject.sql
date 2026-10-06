-- analytics_db: `processed_events.subject` = đối tượng của event (Identity: account_id; Integration: subject).
-- Replay dead-letter áp lại event CŨ; cần biết event nào MỚI hơn (seq lớn hơn) cùng đối tượng đã được áp để không ghi đè
-- cột của projection bằng giá trị cũ. Hàng xử lý trước migration này có subject NULL (không so được => áp như cũ).
-- Forward-only. Không sửa 0001/0002.
ALTER TABLE processed_events ADD COLUMN subject text;
CREATE INDEX processed_events_subject_idx ON processed_events (source, subject, seq) WHERE subject IS NOT NULL;
