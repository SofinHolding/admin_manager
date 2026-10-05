-- integration_db 0002: hàng đợi job cho Fallback Tool (module `jobs`).
-- Claim nguyên tử (FOR UPDATE SKIP LOCKED), lease có hạn, retry có giới hạn -> dead.
-- Xem docs/api/integration-api-v1.md §4.
CREATE TABLE IF NOT EXISTS jobs (
    id                      text PRIMARY KEY,
    owner_account_id        text NOT NULL,
    resource_id             text REFERENCES resources (id),
    platform                text NOT NULL,
    kind                    text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_.]{0,63}$'),
    required_capability     text NOT NULL DEFAULT '',
    status                  text NOT NULL CHECK (status IN ('pending', 'leased', 'done', 'failed', 'dead', 'cancelled')),
    priority                integer NOT NULL DEFAULT 0,
    payload                 jsonb NOT NULL DEFAULT '{}',
    attempts                integer NOT NULL DEFAULT 0,
    max_attempts            integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    run_after               timestamptz NOT NULL,
    lease_until             timestamptz,
    claimed_by_client       text,
    claimed_by_installation text,
    lease_token             text,
    result                  jsonb,
    last_error_code         text,
    last_error_message      text,
    dedupe_key              text,
    created_by              text NOT NULL,
    created_at              timestamptz NOT NULL,
    updated_at              timestamptz NOT NULL,
    completed_at            timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs (platform, priority DESC, run_after, created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS jobs_lease_idx ON jobs (lease_until) WHERE status = 'leased';
CREATE INDEX IF NOT EXISTS jobs_owner_idx ON jobs (owner_account_id, created_at DESC);
-- Chống tạo trùng job đang hoạt động cho cùng một dedupe_key của cùng owner.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe_idx ON jobs (owner_account_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'leased');
