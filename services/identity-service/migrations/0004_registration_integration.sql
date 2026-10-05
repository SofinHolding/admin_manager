-- identity_db 0004: đăng ký kèm Integration (Phase 1.7) và sổ sách consumer feed Integration.
-- Xem docs/architecture/architecture-assessment-v2.md mục 2.3 và docs/api/internal-feed-contract-v1.md §6.

-- Chính sách đăng ký kèm Tool nằm TRÊN registration key; allowed_apps tự cấp permission `<app>:access` (SSO, D11).
ALTER TABLE invite_keys ADD COLUMN IF NOT EXISTS integration_policy jsonb;
ALTER TABLE invite_keys ADD COLUMN IF NOT EXISTS allowed_apps text[] NOT NULL DEFAULT '{}';

-- Yêu cầu provisioning: ghi cùng transaction với account. `client_id` do Identity cấp (cli_ + 24 hex).
CREATE TABLE IF NOT EXISTS integration_provisioning (
    client_id   text PRIMARY KEY CHECK (client_id ~ '^cli_[0-9a-f]{24}$'),
    account_id  text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    tool_name   text NOT NULL,
    client_kind text NOT NULL CHECK (client_kind IN ('primary_tool', 'fallback_tool', 'service')),
    platforms   jsonb NOT NULL DEFAULT '[]'::jsonb,
    scopes      jsonb NOT NULL DEFAULT '[]'::jsonb,
    status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'provisioned', 'failed')),
    error_code  text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS integration_provisioning_account_idx ON integration_provisioning (account_id);

-- Sổ sách consumer (internal-feed-contract-v1.md §6): con trỏ bền, idempotent theo event id, dead-letter.
CREATE TABLE IF NOT EXISTS feed_cursor (
    source text PRIMARY KEY,
    seq    bigint NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS processed_events (
    id           text PRIMARY KEY,
    source       text NOT NULL DEFAULT '',
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dead_letters (
    id         text PRIMARY KEY,
    source     text NOT NULL DEFAULT '',
    type       text NOT NULL,
    error      text NOT NULL,
    attempts   integer NOT NULL,
    payload    jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    replayed_at timestamptz
);
