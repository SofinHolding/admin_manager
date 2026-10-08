-- integration_db 0005: Channel Ownership (PHASE 3) — tự tạo job 'collect' cho Shared Collector khi user có
-- capability:manual_channel_mgmt tự khai báo kênh qua POST /v1/resources (xem legacy_acl/service.py
-- `_ensure_collect_job`, docs/architecture/shared-collector-design-v1.md).
--
-- `jobs.kind` đã là text tự do (CHECK dạng regex `^[a-z][a-z0-9_.]{0,63}$`, không phải enum cố định liệt kê
-- từng giá trị) nên 'collect' dùng được ngay, không cần đổi constraint nào.
--
-- Dedup CHÍNH đã có từ 0002 qua `jobs_dedupe_idx` (owner_account_id, dedupe_key) — `_ensure_collect_job` luôn
-- truyền `dedupe_key = 'collect:' || resource_id`. Index dưới đây là lớp bảo vệ THỨ HAI ở mức DB, độc lập với
-- việc tầng ứng dụng có truyền đúng dedupe_key hay không (vd ai đó gọi thẳng `POST /v1/integrations/jobs` với
-- kind='collect' mà không set dedupe_key): không bao giờ có 2 job 'collect' cùng đang hoạt động
-- (pending/leased) cho cùng một resource.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_collect_resource_active_idx ON jobs (resource_id, kind)
    WHERE kind = 'collect' AND status IN ('pending', 'leased');
