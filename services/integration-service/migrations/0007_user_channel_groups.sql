-- Nhóm quốc gia + kênh do user tự quản lý trên website (mặt phẳng USER).
-- Link với `resources` qua khớp chuỗi `resources.country = user_channel_groups.name` (không khoá ngoại,
-- bảng `resources` giữ nguyên không sửa).
CREATE TABLE IF NOT EXISTS user_channel_groups (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_account_id TEXT NOT NULL,
  name TEXT NOT NULL,
  country_code TEXT NOT NULL DEFAULT '',
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (owner_account_id, name)
);
CREATE INDEX IF NOT EXISTS ucg_owner_idx ON user_channel_groups (owner_account_id);
