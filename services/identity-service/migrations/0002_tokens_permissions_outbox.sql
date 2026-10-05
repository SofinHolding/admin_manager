-- identity_db 0002: khoá ký bất đối xứng (RS256/JWKS), phiên, permission, transactional outbox.
-- Xem docs/api/service-auth-contract-v1.md và docs/api/internal-feed-contract-v1.md.

-- Khoá ký. Khoá private lưu MÃ HOÁ (Fernet, khoá nằm ngoài DB trong IDENTITY_KEY_ENCRYPTION_KEY).
-- Đúng một khoá `active` tại mọi thời điểm (chỉ mục duy nhất một phần).
CREATE TABLE IF NOT EXISTS signing_keys (
    kid               text PRIMARY KEY,
    alg               text NOT NULL DEFAULT 'RS256' CHECK (alg = 'RS256'),
    public_jwk        jsonb NOT NULL,
    private_encrypted text NOT NULL,
    status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    retired_at        timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS signing_keys_one_active ON signing_keys ((true)) WHERE status = 'active';

-- Phiên đăng nhập: thu hồi phiên làm access token mang `sid` đó bị từ chối khi introspect/kiểm tra phiên.
CREATE TABLE IF NOT EXISTS sessions (
    sid         text PRIMARY KEY,
    account_id  text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    client_type text NOT NULL DEFAULT 'web',
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz
);
CREATE INDEX IF NOT EXISTS sessions_account_idx ON sessions (account_id);

ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS sid text REFERENCES sessions (sid) ON DELETE CASCADE;

-- Permission: Identity là nguồn sự thật. Catalog do domain khai báo, grant gắn vào account.
CREATE TABLE IF NOT EXISTS permissions (
    name          text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$'),
    description   text NOT NULL DEFAULT '',
    owner_service text NOT NULL DEFAULT '',
    admin_only    boolean NOT NULL DEFAULT false,   -- true: chỉ role `admin` mới được cấp/thu
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_permissions (
    account_id text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    permission text NOT NULL REFERENCES permissions (name) ON DELETE CASCADE,
    granted_by text NOT NULL DEFAULT '',
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, permission)
);

INSERT INTO permissions (name, description, owner_service, admin_only) VALUES
    ('reward:access', 'Dùng module Reward (tạo/chạy job phân phối điểm)', 'reward-service', false),
    ('reward:admin',  'Quản trị module Reward (xem mọi job/tài khoản)',   'reward-service', true)
ON CONFLICT (name) DO NOTHING;

-- Tài khoản legacy role='discord' mặc định có reward:access (shim tương thích, bỏ ở Phase 6).
INSERT INTO account_permissions (account_id, permission, granted_by)
SELECT id, 'reward:access', 'migration:0002' FROM accounts WHERE role = 'discord'
ON CONFLICT DO NOTHING;

-- Transactional outbox. `seq` là thứ tự commit (xem internal-feed-contract-v1.md §1).
CREATE TABLE IF NOT EXISTS outbox (
    seq          bigserial NOT NULL,
    id           text PRIMARY KEY,
    type         text NOT NULL,
    subject      text NOT NULL,
    data_version integer NOT NULL DEFAULT 1,
    data         jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS outbox_seq_idx ON outbox (seq);
CREATE INDEX IF NOT EXISTS outbox_type_seq_idx ON outbox (type, seq);
