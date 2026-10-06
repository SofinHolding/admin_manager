-- analytics_db: khoá chính read model khớp khoá tự nhiên của Integration.
--   resources: (owner, platform, resource_type, external_id)            [trước: thiếu resource_type]
--   content / metrics / events: thêm resource_type; events thêm khoá resource (resource_external_id)
--     (Integration: UNIQUE(resource_id, external_id) — hai kênh có thể cùng external_id sự kiện/bài).
-- Forward-only. Không sửa 0001. Hàng cũ được điền resource_type từ read_resources khi suy ra duy nhất; sau đó con trỏ
-- canonical.* đặt về 0 để kéo lại toàn bộ (upsert theo revision, idempotent) và seal tăng để cache cũ mồ côi.

ALTER TABLE read_resources ALTER COLUMN resource_type SET DEFAULT '';
UPDATE read_resources SET resource_type = '' WHERE resource_type IS NULL;
ALTER TABLE read_resources DROP CONSTRAINT read_resources_pkey;
ALTER TABLE read_resources ADD PRIMARY KEY (owner_account_id, platform, resource_type, external_id);

ALTER TABLE read_content ADD COLUMN resource_type text NOT NULL DEFAULT '';
ALTER TABLE read_daily_metrics ADD COLUMN resource_type text NOT NULL DEFAULT '';
ALTER TABLE read_events ADD COLUMN resource_type text NOT NULL DEFAULT '';

-- Điền resource_type cho hàng cũ khi (owner, platform, external_id) chỉ có đúng MỘT resource_type trong read_resources.
WITH uniq AS (
    SELECT owner_account_id, platform, external_id, min(resource_type) AS resource_type
      FROM read_resources GROUP BY owner_account_id, platform, external_id HAVING count(*) = 1
)
UPDATE read_content c SET resource_type = u.resource_type FROM uniq u
 WHERE u.owner_account_id = c.owner_account_id AND u.platform = c.platform AND u.external_id = c.resource_external_id;
WITH uniq AS (
    SELECT owner_account_id, platform, external_id, min(resource_type) AS resource_type
      FROM read_resources GROUP BY owner_account_id, platform, external_id HAVING count(*) = 1
)
UPDATE read_daily_metrics c SET resource_type = u.resource_type FROM uniq u
 WHERE u.owner_account_id = c.owner_account_id AND u.platform = c.platform AND u.external_id = c.resource_external_id;
WITH uniq AS (
    SELECT owner_account_id, platform, external_id, min(resource_type) AS resource_type
      FROM read_resources GROUP BY owner_account_id, platform, external_id HAVING count(*) = 1
)
UPDATE read_events c SET resource_type = u.resource_type FROM uniq u
 WHERE u.owner_account_id = c.owner_account_id AND u.platform = c.platform AND u.external_id = c.resource_external_id;

-- Hàng không suy ra được resource_type (mồ côi / mơ hồ) bị xoá: sẽ được kéo lại đúng khoá từ canonical bên dưới
-- (nếu giữ, chúng thành bản ghi ma trùng với hàng mới).
DELETE FROM read_content WHERE resource_type = '';
DELETE FROM read_daily_metrics WHERE resource_type = '';
DELETE FROM read_events WHERE resource_type = '';

ALTER TABLE read_content DROP CONSTRAINT read_content_pkey;
ALTER TABLE read_content ADD PRIMARY KEY (owner_account_id, platform, resource_type, resource_external_id, external_id);
ALTER TABLE read_daily_metrics DROP CONSTRAINT read_daily_metrics_pkey;
ALTER TABLE read_daily_metrics ADD PRIMARY KEY (owner_account_id, platform, resource_type, resource_external_id, metric_date);
ALTER TABLE read_events DROP CONSTRAINT read_events_pkey;
ALTER TABLE read_events ADD PRIMARY KEY (owner_account_id, platform, resource_type, resource_external_id, external_id);

-- Kéo lại canonical từ revision 0 (an toàn: upsert theo revision) và đổi seal.
UPDATE feed_cursor SET seq = 0, updated_at = now() WHERE source LIKE 'canonical.%';
UPDATE sync_state SET num = num + 1, updated_at = now() WHERE key = 'seal';
