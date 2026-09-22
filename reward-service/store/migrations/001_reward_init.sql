-- =====================================================================
-- 001_reward_init.sql — khởi tạo schema reward_* trên DB ufsync
-- BẤT BIẾN: không ALTER/DROP bất kỳ bảng nào của admin_manager
--           (accounts, invite_keys, refresh_tokens, documents, user_meta)
-- =====================================================================

CREATE TABLE IF NOT EXISTS reward_schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ── 1. Thông tin Discord của từng người dùng (1 account = 1 bộ) ──────────
CREATE TABLE IF NOT EXISTS reward_discord_credentials (
    account_id        text PRIMARY KEY,          -- FK mềm → accounts.id (xem B.2.1)
    token_ciphertext  text NOT NULL,             -- Fernet(user token); KHÔNG BAO GIỜ trả qua API
    key_version       integer NOT NULL DEFAULT 1,-- phục vụ xoay khoá mã hoá
    discord_user_id   text,                      -- lấy từ GET /users/@me lúc xác thực token
    discord_username  text,
    guild_id          text,
    channel_id        text,
    command_name      text NOT NULL DEFAULT 'give-xp',
    confirm_mode      text NOT NULL DEFAULT 'reply'
                      CHECK (confirm_mode IN ('off','reply')),
    success_pattern   text,                      -- regex; rỗng => mọi item dừng ở 'unknown'
    failure_pattern   text,
    leveling_bot_id   text,
    delay_ms          integer NOT NULL DEFAULT 3000  CHECK (delay_ms >= 0),
    jitter_ms         integer NOT NULL DEFAULT 500   CHECK (jitter_ms >= 0),
    status            text NOT NULL DEFAULT 'unverified'
                      CHECK (status IN ('unverified','valid','invalid','revoked')),
    last_error        text,
    verified_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- ── 2. Job ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reward_jobs (
    id                      bigserial PRIMARY KEY,
    account_id              text NOT NULL,       -- FK mềm → accounts.id, CHỦ SỞ HỮU job
    name                    text NOT NULL,
    status                  text NOT NULL CHECK (status IN
                            ('draft','validating','validated','invalid',
                             'running','paused','stopping','stopped','completed')),
    -- Ảnh chụp cấu hình tại thời điểm tạo job: sửa credentials sau đó KHÔNG đổi job đang chạy
    guild_id                text NOT NULL,
    channel_id              text NOT NULL,
    command_name            text NOT NULL,
    application_id          text,
    command_id              text,
    command_version         text,
    member_option_name      text NOT NULL DEFAULT 'member',
    member_option_type      smallint NOT NULL DEFAULT 6,
    amount_option_name      text NOT NULL DEFAULT 'amount',
    amount_option_type      smallint NOT NULL DEFAULT 4,
    confirm_mode            text NOT NULL DEFAULT 'reply'
                            CHECK (confirm_mode IN ('off','reply')),
    success_pattern         text,
    failure_pattern         text,
    leveling_bot_id         text,
    delay_ms                integer NOT NULL DEFAULT 3000,
    jitter_ms               integer NOT NULL DEFAULT 500,
    max_item_retries        integer NOT NULL DEFAULT 3,
    unknown_pause_threshold integer NOT NULL DEFAULT 5,
    total_items             integer NOT NULL DEFAULT 0,
    source_name             text,
    source_hash             text,                -- sha256 nội dung đầu vào → cảnh báo nộp trùng
    created_at              timestamptz NOT NULL DEFAULT now(),
    validated_at            timestamptz,
    started_at              timestamptz,
    finished_at             timestamptz
);
CREATE INDEX IF NOT EXISTS ix_reward_jobs_account ON reward_jobs (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reward_jobs_status  ON reward_jobs (status);

-- ── 3. Item (1 dòng "username|point") ───────────────────────────────────
CREATE TABLE IF NOT EXISTS reward_items (
    id                  bigserial PRIMARY KEY,
    job_id              bigint NOT NULL REFERENCES reward_jobs(id) ON DELETE CASCADE,
    row_index           integer NOT NULL,
    raw_username        text NOT NULL,
    normalized_username text NOT NULL,
    resolved_user_id    text,
    point               integer NOT NULL,
    status              text NOT NULL CHECK (status IN
                        ('pending','processing','retrying','success','failed','unknown','skipped')),
    resolve_level       text CHECK (resolve_level IN ('explicit_id','local','member_search')),
    confirmation_level  text CHECK (confirmation_level IN
                        ('none','message_posted','bot_reply_unmatched','bot_reply')),
    failure_code        text,
    failure_message     text,
    attempt_count       integer NOT NULL DEFAULT 0,
    first_sent_at       timestamptz,
    last_attempt_at     timestamptz,
    finalized_at        timestamptz,
    resolved_by         text,                    -- 'system' | 'operator'
    idempotency_key     text NOT NULL
);
-- BẤT BIẾN 2: một (job,row,user,point) chỉ tồn tại DUY NHẤT một lần
CREATE UNIQUE INDEX IF NOT EXISTS ux_reward_items_idem   ON reward_items (idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reward_items_jobrow ON reward_items (job_id, row_index);
CREATE INDEX IF NOT EXISTS ix_reward_items_pick ON reward_items (job_id, status, row_index);

-- ── 4. Attempt (mỗi lần thử gửi 1 item) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS reward_attempts (
    id               bigserial PRIMARY KEY,
    item_id          bigint NOT NULL REFERENCES reward_items(id) ON DELETE CASCADE,
    job_id           bigint NOT NULL,
    attempt_no       integer NOT NULL,
    nonce            text NOT NULL,
    phase            text NOT NULL CHECK (phase IN
                     ('prepared','sent','confirmed','rejected','aborted')),
    command_text     text NOT NULL DEFAULT '',
    http_status      integer,
    message_id       text,      -- slash: id message NEO (mới nhất TRƯỚC khi gọi interactions)
    posted_at        timestamptz,
    reply_message_id text,
    reply_author_id  text,
    reply_excerpt    text,
    link_mode        text,      -- 'reply_ref' | 'bot_id' | 'heuristic'
    error_code       text,
    error_message    text,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz
);
-- BẤT BIẾN 5
CREATE UNIQUE INDEX IF NOT EXISTS ux_reward_attempts_nonce ON reward_attempts (nonce);
CREATE INDEX IF NOT EXISTS ix_reward_attempts_item ON reward_attempts (item_id, attempt_no);
-- index bộ phận phục vụ recovery lúc boot (quét rất nhanh)
CREATE INDEX IF NOT EXISTS ix_reward_attempts_orphan ON reward_attempts (phase)
    WHERE phase IN ('prepared','sent');

-- ── 5. Sổ cái phân phối — BẤT BIẾN 3: PK = item_id ──────────────────────
CREATE TABLE IF NOT EXISTS reward_distributions (
    item_id         bigint PRIMARY KEY REFERENCES reward_items(id) ON DELETE RESTRICT,
    attempt_id      bigint NOT NULL REFERENCES reward_attempts(id),
    job_id          bigint NOT NULL,
    account_id      text NOT NULL,
    discord_user_id text NOT NULL,
    point           integer NOT NULL,
    evidence        jsonb NOT NULL,   -- {level, message_id, reply_message_id, excerpt, link_mode}
    distributed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_reward_dist_job     ON reward_distributions (job_id);
CREATE INDEX IF NOT EXISTS ix_reward_dist_account ON reward_distributions (account_id, distributed_at DESC);

-- ── 6. Vấn đề phát hiện lúc validate ────────────────────────────────────
CREATE TABLE IF NOT EXISTS reward_validation_issues (
    id        bigserial PRIMARY KEY,
    job_id    bigint NOT NULL REFERENCES reward_jobs(id) ON DELETE CASCADE,
    row_index integer,
    severity  text NOT NULL CHECK (severity IN ('error','warning')),
    code      text NOT NULL,
    message   text NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reward_issues_job ON reward_validation_issues (job_id);

-- ── 7. Nhật ký sự kiện job ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reward_job_events (
    id      bigserial PRIMARY KEY,
    job_id  bigint NOT NULL REFERENCES reward_jobs(id) ON DELETE CASCADE,
    at      timestamptz NOT NULL DEFAULT now(),
    type    text NOT NULL,   -- created|validated|started|auto_pause|paused|resumed|stopped|
                             -- completed|recovery|warn_no_pattern|operator_resolve
    actor   text NOT NULL,   -- 'system' | account_id
    payload jsonb
);
CREATE INDEX IF NOT EXISTS ix_reward_events_job ON reward_job_events (job_id, id);

-- ── 8. Lock runner — 1 kênh Discord chỉ 1 job chạy tại một thời điểm ────
CREATE TABLE IF NOT EXISTS reward_runner_lock (
    job_id       bigint PRIMARY KEY,
    account_id   text NOT NULL,
    channel_id   text NOT NULL,
    pid          integer NOT NULL,
    host         text NOT NULL,
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reward_lock_channel ON reward_runner_lock (channel_id);

-- ── 9. Cache username → Discord ID (thay users.json, tách theo account) ──
CREATE TABLE IF NOT EXISTS reward_user_map (
    account_id          text NOT NULL,
    normalized_username text NOT NULL,
    discord_user_id     text NOT NULL,
    source              text NOT NULL DEFAULT 'member_search',
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, normalized_username)
);

-- ── 10. Nhật ký đổi role (admin_manager không ghi việc này) ─────────────
CREATE TABLE IF NOT EXISTS reward_role_audit (
    id               bigserial PRIMARY KEY,
    account_id       text NOT NULL,
    actor_account_id text NOT NULL,
    from_role        text NOT NULL,
    to_role          text NOT NULL,
    reason           text NOT NULL DEFAULT '',
    at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_reward_role_audit_acc ON reward_role_audit (account_id, at DESC);

INSERT INTO reward_schema_migrations (version) VALUES ('001_reward_init')
ON CONFLICT (version) DO NOTHING;
