-- identity_db 0007: gói quyền (preset) trên invite key — docs/architecture/capability-model-v1.md (preset).
-- Forward-only, idempotent. Key tạo bởi client legacy (chỉ gửi role) có preset NULL.
--
-- LƯU Ý VẬN HÀNH: KHÔNG drop bảng/cột ở migration này (ràng buộc vận hành: mọi thay đổi cấu trúc phá huỷ
-- dữ liệu phải qua migration riêng có phương án rollback, do người vận hành phê duyệt). Vì vậy cột
-- `integration_policy` (invite_keys) và các bảng `integration_provisioning`, `feed_cursor`,
-- `processed_events`, `dead_letters` của `feed_consumer` cũ (Phase 1.7, nay đã gỡ khỏi code) GIỮ NGUYÊN
-- trong schema — chỉ không còn được code hiện tại đọc/ghi (xem `registration_keys/service.py`,
-- `registration_keys/repository.py`, `auth/service.py`). Dọn vật lý các đối tượng này để ở một migration
-- dọn dẹp sau, khi có phương án rollback/backup được duyệt riêng.

-- Cột preset: nguồn sự thật cho role + capability khi admin tạo/sửa invite key (registration_keys/presets.py).
ALTER TABLE invite_keys ADD COLUMN IF NOT EXISTS preset text
    CHECK (preset IN ('viewer', 'viewer_tool', 'dash_admin', 'admin', 'discord'));

-- viewer mặc định có thêm capability:manual_channel_mgmt (capability-model-v1.md §3, cập nhật 2026-10-14).
INSERT INTO account_permissions (account_id, permission, granted_by, granted_at)
    SELECT id, 'capability:manual_channel_mgmt', 'migration_0007', NOW()
    FROM accounts
    WHERE role = 'viewer'
    ON CONFLICT DO NOTHING;
