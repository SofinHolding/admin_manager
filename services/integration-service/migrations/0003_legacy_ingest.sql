-- integration_db 0003: schema `legacy_ingest` do Integration sở hữu (Anti-Corruption Layer cho API legacy).
-- Chỉ chứa phần KHÔNG có tương đương canonical: namespace Source, policy platform, Run Queue legacy, pairing.
-- Resource của legacy được ghi vào kho canonical `resources` (cùng đường merge với Tool), KHÔNG lưu ở đây.
-- Dùng chính user integration_svc; không có DB role riêng (xem docs/api/integration-api-v1.md §5).
CREATE SCHEMA IF NOT EXISTS legacy_ingest;

CREATE TABLE IF NOT EXISTS legacy_ingest.platform_policies (
    platform          text PRIMARY KEY,
    web_entry_enabled boolean NOT NULL DEFAULT true,
    fallback_enabled  boolean NOT NULL DEFAULT false,
    adapter_status    text NOT NULL DEFAULT 'none' CHECK (adapter_status IN ('none', 'planned', 'ready')),
    updated_by        text NOT NULL DEFAULT '',
    updated_at        timestamptz NOT NULL DEFAULT now()
);
-- Seed một lần (giống legacy): admin sửa/ thêm platform qua API, không cần đổi code.
INSERT INTO legacy_ingest.platform_policies (platform, web_entry_enabled, fallback_enabled) VALUES
    ('facebook', true, true), ('tiktok', true, true), ('youtube', true, true), ('instagram', true, false)
ON CONFLICT (platform) DO NOTHING;

CREATE TABLE IF NOT EXISTS legacy_ingest.resource_sources (
    id               text PRIMARY KEY,
    owner_account_id text NOT NULL,
    name             text NOT NULL,
    platform         text NOT NULL,
    project_key      text NOT NULL DEFAULT '',
    default_country  text NOT NULL DEFAULT '',
    origin           text NOT NULL DEFAULT 'web_manual',
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS resource_sources_owner_idx ON legacy_ingest.resource_sources (owner_account_id, platform, name);

-- Resource <-> Source. `resource_id` là id canonical (resources.id), cùng schema nên có thể có FK.
CREATE TABLE IF NOT EXISTS legacy_ingest.resource_assignments (
    id                 text PRIMARY KEY,
    resource_id        text NOT NULL REFERENCES public.resources (id),
    source_instance_id text NOT NULL REFERENCES legacy_ingest.resource_sources (id) ON DELETE CASCADE,
    owner_account_id   text NOT NULL,
    enabled            boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL,
    updated_at         timestamptz NOT NULL,
    UNIQUE (resource_id, source_instance_id)
);

CREATE TABLE IF NOT EXISTS legacy_ingest.operation_batches (
    seq        bigserial NOT NULL,
    id         text PRIMARY KEY,
    created_by text NOT NULL,
    platform   text NOT NULL,
    name       text NOT NULL DEFAULT '',
    status     text NOT NULL DEFAULT 'open',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

-- Item của Run Queue legacy. Khi adapter sẵn sàng, item gắn với một job canonical (`job_id`); trạng thái hiển thị suy từ job.
CREATE TABLE IF NOT EXISTS legacy_ingest.operation_items (
    seq                  bigserial NOT NULL,
    id                   text PRIMARY KEY,
    batch_id             text NOT NULL REFERENCES legacy_ingest.operation_batches (id) ON DELETE CASCADE,
    resource_id          text NOT NULL,
    source_assignment_id text NOT NULL DEFAULT '',
    owner_account_id     text NOT NULL,
    platform             text NOT NULL,
    status               text NOT NULL DEFAULT 'queued',
    priority             integer NOT NULL DEFAULT 0,
    attempts             integer NOT NULL DEFAULT 0,
    last_error           text NOT NULL DEFAULT '',
    job_id               text REFERENCES public.jobs (id),
    created_at           timestamptz NOT NULL,
    updated_at           timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS operation_items_batch_idx ON legacy_ingest.operation_items (batch_id, created_at);

CREATE TABLE IF NOT EXISTS legacy_ingest.operation_audit_log (
    id          bigserial PRIMARY KEY,
    account_id  text NOT NULL,
    event_type  text NOT NULL,
    target_type text NOT NULL DEFAULT '',
    target_id   text NOT NULL DEFAULT '',
    payload     jsonb NOT NULL DEFAULT '{}',
    at          timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_ingest.account_links (
    account_id   text NOT NULL,
    tool_user_id text NOT NULL,
    label        text NOT NULL DEFAULT '',
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    linked_at    timestamptz NOT NULL,
    PRIMARY KEY (account_id, tool_user_id)
);

CREATE TABLE IF NOT EXISTS legacy_ingest.link_codes (
    code       text PRIMARY KEY,
    account_id text NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'used', 'expired'))
);
