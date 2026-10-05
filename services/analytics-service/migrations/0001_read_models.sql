-- analytics_db: read model dựng từ canonical export của Integration + cấu hình dashboard + sổ sách consumer.
-- Forward-only. KHÔNG sửa file này sau khi đã áp dụng (tạo 0002_... mới).

-- ── Sổ sách consumer ────────────────────────────────────────────────────────────

-- Trạng thái đồng bộ dạng khoá-giá trị. Hàng 'seal' giữ SỐ PHIÊN BẢN dùng trong khoá cache:
-- tăng TRONG CÙNG transaction với mọi thay đổi ảnh hưởng số liệu (áp revision, sửa nhóm, sửa alias).
CREATE TABLE sync_state (
    key         text PRIMARY KEY,
    num         bigint NOT NULL DEFAULT 0,
    value       jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO sync_state (key, num) VALUES ('seal', 1);

-- Con trỏ đọc: 'integration' / 'identity' = seq của change feed; 'canonical.<kind>' = revision đã áp dụng.
CREATE TABLE feed_cursor (
    source      text PRIMARY KEY,
    seq         bigint NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE processed_events (
    id            text PRIMARY KEY,
    source        text NOT NULL,
    seq           bigint NOT NULL,
    type          text NOT NULL,
    processed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dead_letters (
    id               text PRIMARY KEY,
    source           text NOT NULL,
    seq              bigint,
    type             text NOT NULL,
    error            text NOT NULL,
    attempts         integer NOT NULL DEFAULT 1,
    payload          jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_attempt_at  timestamptz NOT NULL DEFAULT now(),
    replayed_at      timestamptz
);
CREATE INDEX dead_letters_open ON dead_letters (created_at) WHERE replayed_at IS NULL;

-- Bản sao cục bộ (nuôi bằng feed account của Identity), KHÔNG phải bảng của Identity.
CREATE TABLE account_projection (
    account_id  text PRIMARY KEY,
    username    text NOT NULL DEFAULT '',
    role        text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT '',
    deleted     boolean NOT NULL DEFAULT false,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ── Read model (canonical committed của Integration) ────────────────────────────
-- `removed` giữ nguyên cờ nguồn (lịch sử còn, số liệu loại ra). `revision` là revision canonical toàn cục.

CREATE TABLE read_projects (
    owner_account_id  text NOT NULL,
    project_key       text NOT NULL,
    name              text NOT NULL DEFAULT '',
    platform          text NOT NULL DEFAULT '',
    country           text NOT NULL DEFAULT '',
    enabled           boolean NOT NULL DEFAULT true,
    observed_at       timestamptz,
    revision          bigint NOT NULL,
    removed           boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_account_id, project_key)
);

CREATE TABLE read_resources (
    owner_account_id  text NOT NULL,
    platform          text NOT NULL,
    external_id       text NOT NULL,
    resource_type     text NOT NULL DEFAULT '',
    client_id         text NOT NULL DEFAULT '',
    display_name      text NOT NULL DEFAULT '',
    username          text NOT NULL DEFAULT '',
    url               text NOT NULL DEFAULT '',
    country           text NOT NULL DEFAULT '',
    state             text NOT NULL DEFAULT 'active',
    projects          jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{project_key, enabled}]
    managed           boolean,                               -- NULL = suy ra từ liên kết chủ đề
    active            boolean,
    observed_at       timestamptz,
    revision          bigint NOT NULL,
    removed           boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_account_id, platform, external_id)
);
CREATE INDEX read_resources_owner ON read_resources (owner_account_id) WHERE NOT removed;

CREATE TABLE read_content (
    owner_account_id       text NOT NULL,
    platform               text NOT NULL,
    resource_external_id   text NOT NULL,
    external_id            text NOT NULL,
    published_date         date NOT NULL,
    views                  bigint,            -- NULL ≠ 0
    reactions              bigint,
    comments               bigint,
    state                  text NOT NULL DEFAULT 'active',
    observed_at            timestamptz,
    revision               bigint NOT NULL,
    removed                boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_account_id, platform, resource_external_id, external_id)
);
CREATE INDEX read_content_day ON read_content (owner_account_id, published_date) WHERE NOT removed;

CREATE TABLE read_daily_metrics (
    owner_account_id       text NOT NULL,
    platform               text NOT NULL,
    resource_external_id   text NOT NULL,
    metric_date            date NOT NULL,
    views                  bigint,            -- NULL ≠ 0
    metrics                jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at            timestamptz,
    revision               bigint NOT NULL,
    removed                boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_account_id, platform, resource_external_id, metric_date)
);
CREATE INDEX read_daily_day ON read_daily_metrics (owner_account_id, metric_date) WHERE NOT removed;

CREATE TABLE read_events (
    owner_account_id       text NOT NULL,
    external_id            text NOT NULL,
    platform               text NOT NULL DEFAULT '',
    resource_external_id   text NOT NULL DEFAULT '',
    kind                   text NOT NULL,
    occurred_date          date NOT NULL,
    project_key            text NOT NULL DEFAULT '',
    observed_at            timestamptz,
    revision               bigint NOT NULL,
    removed                boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_account_id, external_id)
);
CREATE INDEX read_events_day ON read_events (owner_account_id, occurred_date) WHERE NOT removed;

-- ── Cấu hình dashboard do người dùng / admin quản lý ────────────────────────────

CREATE TABLE channel_groups (
    id          text PRIMARY KEY,
    account_id  text NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX channel_groups_owner ON channel_groups (account_id);

CREATE TABLE channel_group_members (
    group_id          text NOT NULL REFERENCES channel_groups (id) ON DELETE CASCADE,
    channel_ref       text NOT NULL,
    platform_tag      text NOT NULL DEFAULT '',
    country_override  text NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, channel_ref)
);

CREATE TABLE country_aliases (
    alias       text PRIMARY KEY,           -- luôn lower-case, đã trim
    canonical   text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE country_canonicals (
    name        text PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now()
);
