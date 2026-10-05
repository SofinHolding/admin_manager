-- identity_db 0003: OIDC / OAuth2 (Authorization Code + PKCE), phiên SSO của trang đăng nhập hosted,
-- refresh token xoay vòng theo FAMILY, back-channel logout.
-- Tham chiếu: RFC 6749/6750/7009/7636/7662, OpenID Connect Core 1.0, OIDC Back-Channel Logout 1.0.
-- Mọi bí mật (secret client, auth code, refresh token, cookie SSO) chỉ lưu dạng HASH sha256.

CREATE TABLE IF NOT EXISTS oidc_clients (
    client_id                 text PRIMARY KEY CHECK (client_id ~ '^[A-Za-z0-9_.-]{3,64}$'),
    name                      text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    type                      text NOT NULL CHECK (type IN ('public', 'confidential')),
    secret_hash               text NOT NULL DEFAULT '',
    redirect_uris             text[] NOT NULL CHECK (cardinality(redirect_uris) >= 1),
    post_logout_redirect_uris text[] NOT NULL DEFAULT '{}',
    backchannel_logout_uri    text NOT NULL DEFAULT '',
    allowed_scopes            text[] NOT NULL DEFAULT ARRAY['openid', 'profile', 'email'],
    audience                  text NOT NULL DEFAULT '',
    first_party               boolean NOT NULL DEFAULT false,
    status                    text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT oidc_clients_secret_for_confidential CHECK (type = 'public' OR secret_hash <> ''),
    CONSTRAINT oidc_clients_public_without_secret CHECK (type = 'confidential' OR secret_hash = '')
);

-- Phiên đăng nhập SSO của trang hosted. Bản thân phiên là một dòng trong `sessions` (client_type='sso') nên
-- đổi mật khẩu / khoá tài khoản / thu hồi phiên có hiệu lực ngay trên cookie SSO.
CREATE TABLE IF NOT EXISTS sso_sessions (
    token_hash text PRIMARY KEY,
    sid        text NOT NULL REFERENCES sessions (sid) ON DELETE CASCADE,
    account_id text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    auth_time  timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sso_sessions_sid_idx ON sso_sessions (sid);

CREATE TABLE IF NOT EXISTS oidc_auth_codes (
    code_hash             text PRIMARY KEY,
    client_id             text NOT NULL REFERENCES oidc_clients (client_id) ON DELETE CASCADE,
    account_id            text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    sid                   text NOT NULL,   -- phiên cấp cho grant này; dòng `sessions` chỉ được tạo khi ĐỔI code
    sso_sid               text,
    family_id             text NOT NULL,
    redirect_uri          text NOT NULL,
    code_challenge        text NOT NULL DEFAULT '',
    code_challenge_method text NOT NULL DEFAULT 'S256' CHECK (code_challenge_method = 'S256'),
    nonce                 text NOT NULL DEFAULT '',
    scope                 text NOT NULL,
    auth_time             timestamptz NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    expires_at            timestamptz NOT NULL,
    used_at               timestamptz
);
CREATE INDEX IF NOT EXISTS oidc_auth_codes_family_idx ON oidc_auth_codes (family_id);

-- Refresh token OIDC: xoay vòng theo family. `used_at` != NULL nghĩa là đã được đổi lấy token mới;
-- xuất trình lại token đã dùng = dấu hiệu bị đánh cắp -> thu hồi cả family và phiên.
CREATE TABLE IF NOT EXISTS oidc_refresh_tokens (
    token_hash text PRIMARY KEY,
    family_id  text NOT NULL,
    client_id  text NOT NULL REFERENCES oidc_clients (client_id) ON DELETE CASCADE,
    account_id text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    sid        text NOT NULL REFERENCES sessions (sid) ON DELETE CASCADE,
    scope      text NOT NULL,
    auth_time  timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS oidc_refresh_family_idx ON oidc_refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS oidc_refresh_sid_idx ON oidc_refresh_tokens (sid);

-- Client nào đã nhận token cho phiên (sid) nào: căn cứ để gửi back-channel logout. KHÔNG khoá ngoại tới
-- accounts/sessions để xoá tài khoản vẫn còn biết cần báo cho client nào.
CREATE TABLE IF NOT EXISTS oidc_session_clients (
    sid        text NOT NULL,
    client_id  text NOT NULL REFERENCES oidc_clients (client_id) ON DELETE CASCADE,
    account_id text NOT NULL,
    sso_sid    text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sid, client_id)
);
CREATE INDEX IF NOT EXISTS oidc_session_clients_account_idx ON oidc_session_clients (account_id);
CREATE INDEX IF NOT EXISTS oidc_session_clients_sso_idx ON oidc_session_clients (sso_sid);

-- Con trỏ riêng của worker back-channel đọc outbox của CHÍNH identity.
CREATE TABLE IF NOT EXISTS oidc_cursor (
    name text PRIMARY KEY,
    seq  bigint NOT NULL DEFAULT 0
);

-- Hàng đợi gửi logout_token. Mỗi (client, sid) đúng một lần giao; hết lượt thử -> status='dead' (dead-letter).
CREATE TABLE IF NOT EXISTS oidc_backchannel_deliveries (
    id              bigserial PRIMARY KEY,
    event_id        text NOT NULL,
    client_id       text NOT NULL REFERENCES oidc_clients (client_id) ON DELETE CASCADE,
    account_id      text NOT NULL,
    sid             text NOT NULL,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'dead')),
    attempts        integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error      text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    delivered_at    timestamptz,
    CONSTRAINT oidc_backchannel_once UNIQUE (client_id, sid)
);
CREATE INDEX IF NOT EXISTS oidc_backchannel_due_idx ON oidc_backchannel_deliveries (status, next_attempt_at);
