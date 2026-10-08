-- integration_db 0006: Delegated ingestion cho Shared Collector (PHASE 4, mở rộng PHASE 3 — xem
-- docs/architecture/shared-collector-design-v1.md §3-4).
--
-- `required_capability` đã có từ 0002 (dùng ngay cho `collect`, xem 0005) nên KHÔNG thêm lại ở đây.
--
-- `resource_owner_id`: owner THỰC của dữ liệu khi job được Shared Collector push hộ qua header
-- `X-Delegated-Job` (snapshots/router.py `_delegated`). Với job 'collect' tự tạo (legacy_acl/service.py
-- `_ensure_collect_job`), giá trị này LUÔN bằng `owner_account_id` (chủ resource) — hai cột tách riêng vì mang hai
-- Ý NGHĨA khác nhau: `owner_account_id` = ai "sở hữu" hàng đợi job (quota/dedupe/visibility), `resource_owner_id` =
-- dữ liệu push qua header X-Delegated-Job thuộc về ai. NULL = job không hỗ trợ delegated ingestion (mọi job tạo
-- trước PHASE 4, và mọi job không phải 'collect', giữ nguyên hành vi cũ — owner suy từ chính credential).
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS resource_owner_id text;

COMMENT ON COLUMN jobs.resource_owner_id IS
    'Owner thực của dữ liệu khi collector push hộ qua X-Delegated-Job (NULL = không hỗ trợ delegated ingestion).';
