-- Migration 0005: Capability permissions
-- Thêm capability permissions vào catalog và cột capability_grant vào invite_keys.
-- Không phá vỡ dữ liệu cũ.

-- 1. Seed capability permissions vào catalog
INSERT INTO permissions (name, description, owner_service, admin_only) VALUES
  ('capability:web_access',          'Đăng nhập và dùng website cơ bản',           'identity', false),
  ('capability:dashboard_access',    'Xem Dashboard thống kê',                     'identity', false),
  ('capability:manual_channel_mgmt', 'Tự nhập/sửa/xóa kênh trên website',          'identity', false),
  ('capability:tool_integration',    'Liên kết Tool, tạo integration client',       'identity', false),
  ('capability:api_write',           'Credential gửi được snapshots/metrics',       'identity', false),
  ('capability:api_read',            'Credential đọc được dữ liệu',                'identity', false),
  ('capability:multi_tool',          'Tạo nhiều hơn 1 integration client',          'identity', false)
ON CONFLICT (name) DO NOTHING;

-- 2. Thêm cột capability_grant vào invite_keys
ALTER TABLE invite_keys
  ADD COLUMN IF NOT EXISTS capability_grant JSONB;

-- 3. Grant default capabilities cho tài khoản đã tồn tại theo role
-- viewer/dash_admin: web_access + dashboard_access
INSERT INTO account_permissions (account_id, permission, granted_by, granted_at)
  SELECT id, 'capability:web_access', 'system:migration-0005', NOW()
  FROM accounts
  WHERE role IN ('viewer', 'dash_admin', 'admin')
  ON CONFLICT DO NOTHING;

INSERT INTO account_permissions (account_id, permission, granted_by, granted_at)
  SELECT id, 'capability:dashboard_access', 'system:migration-0005', NOW()
  FROM accounts
  WHERE role IN ('viewer', 'dash_admin', 'admin')
  ON CONFLICT DO NOTHING;

-- admin: tất cả capabilities
INSERT INTO account_permissions (account_id, permission, granted_by, granted_at)
  SELECT a.id, p.name, 'system:migration-0005', NOW()
  FROM accounts a
  CROSS JOIN (SELECT name FROM permissions WHERE name LIKE 'capability:%') p
  WHERE a.role = 'admin'
  ON CONFLICT DO NOTHING;
