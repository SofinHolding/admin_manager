-- 004 — Bảng sổ sách để Reward phản ứng với sự kiện của Identity (Phase 4: gỡ coupling DB).
-- Reward KHÔNG đọc/ghi bảng của Identity; mọi dữ liệu về tài khoản đến từ feed `/internal/v1/feed`
-- (docs/api/internal-feed-contract-v1.md §6) và nằm trong các bảng RIÊNG dưới đây.

-- Con trỏ feed bền: mỗi nguồn một dòng (`source` = 'identity').
CREATE TABLE IF NOT EXISTS feed_cursor (
    source      text PRIMARY KEY,
    seq         bigint NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Idempotent theo `id` sự kiện: xử lý lại event đã thấy là no-op.
CREATE TABLE IF NOT EXISTS processed_events (
    id            text PRIMARY KEY,
    type          text NOT NULL,
    subject       text NOT NULL DEFAULT '',
    seq           bigint,
    processed_at  timestamptz NOT NULL DEFAULT now()
);

-- Event xử lý lỗi quá số lần cho phép: ghi lại, bỏ qua để KHÔNG chặn hàng đợi, replay bằng lệnh riêng.
CREATE TABLE IF NOT EXISTS dead_letters (
    id           text PRIMARY KEY,
    type         text NOT NULL,
    error        text NOT NULL,
    attempts     integer NOT NULL DEFAULT 0,
    payload      jsonb NOT NULL,
    seq          bigint,
    created_at   timestamptz NOT NULL DEFAULT now(),
    replayed_at  timestamptz
);

-- Read model cục bộ về tài khoản (nuôi bằng event, chỉ để hiển thị/đối chiếu — KHÔNG phải nguồn quyền).
CREATE TABLE IF NOT EXISTS account_projection (
    account_id  text PRIMARY KEY,
    username    text NOT NULL DEFAULT '',
    role        text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'active',
    reward_access boolean NOT NULL DEFAULT false,   -- permission `reward:access` cấp riêng (theo event permission_changed)
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Denylist cục bộ: chặn NGAY (trước khi access token hết hạn) tài khoản bị thu quyền/khoá/xoá/thu hồi phiên.
--   reason ∈ permission_revoked | suspended | deleted  → chặn mọi token của tài khoản (gỡ khi có event ngược lại)
--   reason = session_revoked → chỉ chặn token có `sid` khớp, hoặc (sid NULL) token phát hành trước/đúng `denied_at`
CREATE TABLE IF NOT EXISTS account_denylist (
    id          bigserial PRIMARY KEY,
    account_id  text NOT NULL,
    reason      text NOT NULL,
    sid         text NOT NULL DEFAULT '',
    denied_at   timestamptz NOT NULL DEFAULT now(),
    event_id    text
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_account_denylist ON account_denylist (account_id, reason, sid);
CREATE INDEX IF NOT EXISTS ix_account_denylist_acc ON account_denylist (account_id);

-- Audit riêng của Reward cho thao tác quản trị: ghi thêm kết quả gọi API Identity.
ALTER TABLE reward_role_audit ADD COLUMN IF NOT EXISTS outcome text NOT NULL DEFAULT 'applied';
ALTER TABLE reward_role_audit ADD COLUMN IF NOT EXISTS identity_status integer;

INSERT INTO reward_schema_migrations (version) VALUES ('004_reward_identity_link')
ON CONFLICT (version) DO NOTHING;
