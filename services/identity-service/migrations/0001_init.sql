-- identity_db 0001: account, registration key, refresh token, audit.
-- Khác legacy (admin-server/db.py _PG_DDL), có chủ đích:
--   * thời gian là timestamptz (legacy lưu text ISO);
--   * mọi token (verify, reset, refresh) lưu dạng HASH sha256, không lưu bản rõ;
--   * mã đổi mật khẩu 6 số tách riêng khỏi token reset (sửa lỗ hổng dò mã của legacy);
--   * ràng buộc CHECK cho role/status/used_count; refresh_token có FK tới accounts;
--   * id giữ định dạng uuid4 hex của legacy để token cũ (claim `sub`) vẫn khớp sau khi migrate.

CREATE TABLE IF NOT EXISTS invite_keys (
    key         text PRIMARY KEY,
    role        text NOT NULL DEFAULT 'viewer'
                CHECK (role IN ('viewer', 'admin', 'discord', 'dash_admin')),
    label       text NOT NULL DEFAULT '',
    max_uses    integer NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
    used_count  integer NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz,
    status      text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'suspended', 'revoked')),
    CONSTRAINT invite_keys_used_le_max CHECK (used_count <= max_uses)
);

CREATE TABLE IF NOT EXISTS accounts (
    id                text PRIMARY KEY,
    username          text NOT NULL,
    email             text NOT NULL,
    password_hash     text NOT NULL,
    role              text NOT NULL DEFAULT 'viewer'
                      CHECK (role IN ('viewer', 'admin', 'discord', 'dash_admin')),
    invite_key        text NOT NULL,
    email_verified    boolean NOT NULL DEFAULT false,
    verify_token_hash text NOT NULL DEFAULT '',
    verify_token_at   timestamptz,
    reset_token_hash  text NOT NULL DEFAULT '',
    reset_token_at    timestamptz,
    -- Mã 6 số đổi mật khẩu TÁCH RIÊNG khỏi token đặt lại mật khẩu. Legacy dùng chung một cột nên mã 6 số
    -- có thể được dùng ở /reset-password (không cần đăng nhập, không rate limit) -> dò 10^6 mã chiếm tài khoản.
    change_code_hash     text NOT NULL DEFAULT '',
    change_code_at       timestamptz,
    change_code_attempts integer NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_login        timestamptz,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'active', 'suspended')),
    CONSTRAINT accounts_username_key UNIQUE (username),
    CONSTRAINT accounts_email_key UNIQUE (email),
    CONSTRAINT accounts_email_lowercase CHECK (email = lower(email))
);

CREATE INDEX IF NOT EXISTS accounts_verify_token_idx ON accounts (verify_token_hash)
    WHERE verify_token_hash <> '';
CREATE INDEX IF NOT EXISTS accounts_reset_token_idx ON accounts (reset_token_hash)
    WHERE reset_token_hash <> '';

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash  text PRIMARY KEY,
    account_id  text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    revoked     boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS refresh_tokens_account_idx ON refresh_tokens (account_id);

CREATE TABLE IF NOT EXISTS auth_audit (
    id          bigserial PRIMARY KEY,
    account_id  text NOT NULL,
    event_type  text NOT NULL,
    target_type text NOT NULL DEFAULT '',
    target_id   text NOT NULL DEFAULT '',
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS auth_audit_account_idx ON auth_audit (account_id, at DESC);
