-- integration_db 0001: client/credential/installation, registry (dữ liệu), kho canonical, snapshot + staging,
-- idempotency, audit, outbox và sổ sách consumer provisioning.
-- Xem docs/api/integration-api-v1.md, ingest-data-contract-v1.md, internal-feed-contract-v1.md.

-- ── Revision canonical toàn cục (gán lúc commit, dưới khoá advisory của outbox: xem feed/repository.py) ──
CREATE SEQUENCE IF NOT EXISTS canonical_revision_seq AS bigint START 1;

-- ── Client / credential / installation ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS integration_clients (
    id                 text PRIMARY KEY CHECK (id ~ '^cli_[A-Za-z0-9]{8,64}$'),
    owner_account_id   text NOT NULL,
    tool_name          text NOT NULL,
    client_kind        text NOT NULL CHECK (client_kind IN ('primary_tool', 'fallback_tool', 'service')),
    status             text NOT NULL CHECK (status IN ('pending_email_verification', 'active', 'suspended', 'deleted')),
    suspended_by       text CHECK (suspended_by IN ('account', 'admin')),
    contract_version   text NOT NULL DEFAULT '2026-10-01',
    allowed_platforms  text[] NOT NULL DEFAULT '{}',
    allowed_scopes     text[] NOT NULL DEFAULT '{}',
    created_from       text NOT NULL DEFAULT 'api' CHECK (created_from IN ('api', 'provisioning')),
    created_at         timestamptz NOT NULL,
    updated_at         timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS integration_clients_owner_idx ON integration_clients (owner_account_id);

CREATE TABLE IF NOT EXISTS integration_credentials (
    key_id        text PRIMARY KEY CHECK (key_id ~ '^[A-Za-z0-9_-]{12}$'),
    client_id     text NOT NULL REFERENCES integration_clients (id) ON DELETE CASCADE,
    secret_hash   text NOT NULL CHECK (secret_hash ~ '^[0-9a-f]{64}$'),
    scopes        text[] NOT NULL DEFAULT '{}',
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    label         text NOT NULL DEFAULT '',
    expires_at    timestamptz,
    last_used_at  timestamptz,
    created_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    revoke_reason text,
    rotated_from  text
);
CREATE INDEX IF NOT EXISTS integration_credentials_client_idx ON integration_credentials (client_id);

CREATE TABLE IF NOT EXISTS installations (
    id            text PRIMARY KEY,
    client_id     text NOT NULL REFERENCES integration_clients (id) ON DELETE CASCADE,
    device_id     text NOT NULL,
    hostname      text NOT NULL DEFAULT '',
    os            text NOT NULL DEFAULT '',
    app_version   text NOT NULL DEFAULT '',
    capabilities  text[] NOT NULL DEFAULT '{}',
    status        text NOT NULL DEFAULT 'active',
    first_seen_at timestamptz NOT NULL,
    last_seen_at  timestamptz NOT NULL,
    last_ip       text NOT NULL DEFAULT '',
    UNIQUE (client_id, device_id)
);

-- ── Registry: DỮ LIỆU, không phải code ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_registry (
    key          text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{0,63}$'),
    display_name text NOT NULL DEFAULT '',
    enabled      boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS metric_registry (
    key          text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{0,63}$'),
    display_name text NOT NULL DEFAULT '',
    description  text NOT NULL DEFAULT '',
    enabled      boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
INSERT INTO platform_registry (key, display_name) VALUES
    ('facebook', 'Facebook'), ('youtube', 'YouTube'), ('tiktok', 'TikTok'), ('instagram', 'Instagram')
ON CONFLICT (key) DO NOTHING;
INSERT INTO metric_registry (key, display_name, description) VALUES
    ('views', 'Views', 'Lượt xem (số tuyệt đối tại observed_at)'),
    ('reactions', 'Reactions', 'Tương tác/cảm xúc (khoá canonical cho "tương tác" của Dashboard)'),
    ('comments', 'Comments', 'Bình luận'),
    ('likes', 'Likes', 'Lượt thích'),
    ('shares', 'Shares', 'Chia sẻ'),
    ('followers', 'Followers', 'Người theo dõi'),
    ('interactions', 'Interactions', 'Tổng tương tác'),
    ('videos_posted', 'Videos posted', 'Số video đã đăng')
ON CONFLICT (key) DO NOTHING;

-- ── Kho canonical. Mọi bản ghi mang `revision` (gán lúc commit) và cờ `removed` (lịch sử giữ lại) ──
CREATE TABLE IF NOT EXISTS projects (
    id               text PRIMARY KEY,
    owner_account_id text NOT NULL,
    client_id        text NOT NULL,
    project_key      text NOT NULL CHECK (project_key ~ '^[a-z][a-z0-9_]{0,63}$'),
    platform         text,
    name             text NOT NULL,
    country_code     text,
    enabled          boolean NOT NULL DEFAULT true,
    metadata         jsonb NOT NULL DEFAULT '{}',
    removed          boolean NOT NULL DEFAULT false,
    revision         bigint NOT NULL,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    UNIQUE (owner_account_id, project_key)
);
CREATE INDEX IF NOT EXISTS projects_revision_idx ON projects (revision);

CREATE TABLE IF NOT EXISTS resources (
    id               text PRIMARY KEY,
    owner_account_id text NOT NULL,
    client_id        text NOT NULL,
    platform         text NOT NULL,
    resource_type    text NOT NULL,
    external_id      text NOT NULL,
    state            text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'disabled')),
    origin           text NOT NULL DEFAULT 'tool',
    execution_mode   text NOT NULL DEFAULT 'primary_tool',
    observed_at      timestamptz NOT NULL,
    data             jsonb NOT NULL DEFAULT '{}',
    removed          boolean NOT NULL DEFAULT false,
    revision         bigint NOT NULL,
    last_snapshot_id text,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    UNIQUE (owner_account_id, platform, resource_type, external_id)
);
CREATE INDEX IF NOT EXISTS resources_revision_idx ON resources (revision);
CREATE INDEX IF NOT EXISTS resources_lookup_idx ON resources (owner_account_id, platform, external_id);

CREATE TABLE IF NOT EXISTS resource_project_bindings (
    resource_id text NOT NULL REFERENCES resources (id) ON DELETE CASCADE,
    project_id  text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    enabled     boolean NOT NULL DEFAULT true,
    revision    bigint NOT NULL,
    PRIMARY KEY (resource_id, project_id)
);

CREATE TABLE IF NOT EXISTS content_items (
    id               text PRIMARY KEY,
    owner_account_id text NOT NULL,
    client_id        text NOT NULL,
    platform         text NOT NULL,
    resource_id      text NOT NULL REFERENCES resources (id),
    external_id      text NOT NULL,
    published_date   date NOT NULL,
    state            text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'deleted')),
    observed_at      timestamptz NOT NULL,
    data             jsonb NOT NULL DEFAULT '{}',
    removed          boolean NOT NULL DEFAULT false,
    revision         bigint NOT NULL,
    last_snapshot_id text,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    UNIQUE (resource_id, external_id)
);
CREATE INDEX IF NOT EXISTS content_items_revision_idx ON content_items (revision);
CREATE INDEX IF NOT EXISTS content_items_scope_idx ON content_items (owner_account_id, client_id, platform, published_date);

CREATE TABLE IF NOT EXISTS daily_metrics (
    id               text PRIMARY KEY,
    owner_account_id text NOT NULL,
    client_id        text NOT NULL,
    platform         text NOT NULL,
    resource_id      text NOT NULL REFERENCES resources (id),
    metric_date      date NOT NULL,
    observed_at      timestamptz NOT NULL,
    data             jsonb NOT NULL DEFAULT '{}',
    removed          boolean NOT NULL DEFAULT false,
    revision         bigint NOT NULL,
    last_snapshot_id text,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    UNIQUE (resource_id, metric_date)
);
CREATE INDEX IF NOT EXISTS daily_metrics_revision_idx ON daily_metrics (revision);
CREATE INDEX IF NOT EXISTS daily_metrics_scope_idx ON daily_metrics (owner_account_id, client_id, platform, metric_date);

CREATE TABLE IF NOT EXISTS activity_events (
    id               text PRIMARY KEY,
    owner_account_id text NOT NULL,
    client_id        text NOT NULL,
    platform         text NOT NULL,
    resource_id      text NOT NULL REFERENCES resources (id),
    external_id      text NOT NULL,
    kind             text NOT NULL,
    occurred_date    date NOT NULL,
    observed_at      timestamptz NOT NULL,
    data             jsonb NOT NULL DEFAULT '{}',
    removed          boolean NOT NULL DEFAULT false,
    revision         bigint NOT NULL,
    last_snapshot_id text,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    UNIQUE (resource_id, external_id)
);
CREATE INDEX IF NOT EXISTS activity_events_revision_idx ON activity_events (revision);
CREATE INDEX IF NOT EXISTS activity_events_scope_idx ON activity_events (owner_account_id, client_id, platform, occurred_date);

-- ── Snapshot (state machine) + staging ─────────────────────────────────────────
-- building -> committing -> committed | failed | abandoned. `is_current` chỉ đổi khi commit thành công.
CREATE TABLE IF NOT EXISTS snapshots (
    id               text PRIMARY KEY,
    owner_account_id text NOT NULL,
    client_id        text NOT NULL,
    installation_id  text,
    platform         text NOT NULL,
    snapshot_type    text NOT NULL CHECK (snapshot_type IN ('full', 'delta')),
    status           text NOT NULL CHECK (status IN ('building', 'committing', 'committed', 'failed', 'abandoned')),
    observed_at      timestamptz NOT NULL,
    report_timezone  text NOT NULL,
    scope            jsonb,
    expected_counts  jsonb NOT NULL DEFAULT '{}',
    accept_partial   boolean NOT NULL DEFAULT false,
    is_current       boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    committed_at     timestamptz,
    result           jsonb,
    failure_reason   text,
    revision_from    bigint,
    revision_to      bigint
);
CREATE INDEX IF NOT EXISTS snapshots_scope_idx ON snapshots (owner_account_id, client_id, platform, snapshot_type, status);
CREATE INDEX IF NOT EXISTS snapshots_status_idx ON snapshots (status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS snapshots_one_current ON snapshots (owner_account_id, client_id, platform, snapshot_type)
    WHERE is_current;

CREATE TABLE IF NOT EXISTS snapshot_items (
    id          bigserial PRIMARY KEY,
    snapshot_id text NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('resources', 'content', 'metrics', 'events')),
    item_key    text NOT NULL,
    status      text NOT NULL CHECK (status IN ('staged', 'rejected')),
    observed_at timestamptz,
    payload     jsonb,
    idx         integer,
    external_id text,
    code        text,
    message     text
);
CREATE UNIQUE INDEX IF NOT EXISTS snapshot_items_staged_key ON snapshot_items (snapshot_id, kind, item_key)
    WHERE status = 'staged';
CREATE INDEX IF NOT EXISTS snapshot_items_snapshot_idx ON snapshot_items (snapshot_id, status);

-- ── Idempotency: (client, method, path, key) -> (sha256(body), response) ────────
CREATE TABLE IF NOT EXISTS idempotency_keys (
    client_id    text NOT NULL,
    method       text NOT NULL,
    path         text NOT NULL,
    idem_key     text NOT NULL,
    request_hash text NOT NULL,
    status_code  integer,
    response     text,            -- text (không phải jsonb) để phát lại ĐÚNG từng byte phản hồi cũ
    created_at   timestamptz NOT NULL,
    PRIMARY KEY (client_id, method, path, idem_key)
);
CREATE INDEX IF NOT EXISTS idempotency_created_idx ON idempotency_keys (created_at);

-- ── Audit ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id               bigserial PRIMARY KEY,
    account_id       text,
    actor_account_id text,
    client_id        text,
    installation_id  text,
    key_id           text,
    action           text NOT NULL,
    snapshot_id      text,
    resource_id      text,
    request_id       text NOT NULL DEFAULT '-',
    ip               text NOT NULL DEFAULT '',
    result           text NOT NULL,
    status_code      integer,
    detail           jsonb NOT NULL DEFAULT '{}',
    at               timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);
CREATE INDEX IF NOT EXISTS audit_log_client_idx ON audit_log (client_id, at DESC);

-- ── Read model cục bộ nuôi bằng feed Identity (KHÔNG gọi Identity trên đường request) ──
CREATE TABLE IF NOT EXISTS account_projection (
    account_id     text PRIMARY KEY,
    username       text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active',
    email_verified boolean NOT NULL DEFAULT false,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ── Transactional outbox. `seq` là thứ tự commit (internal-feed-contract-v1.md §1) ──
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

-- ── Sổ sách consumer (internal-feed-contract-v1.md §6) ─────────────────────────
CREATE TABLE IF NOT EXISTS feed_cursor (
    source     text PRIMARY KEY,
    seq        bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS processed_events (
    id           text PRIMARY KEY,
    type         text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dead_letters (
    id          text PRIMARY KEY,
    type        text NOT NULL,
    error       text NOT NULL,
    attempts    integer NOT NULL,
    payload     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
