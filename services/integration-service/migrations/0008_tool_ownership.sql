-- integration_db 0008: ownership (personal/shared) của integration_clients, wizard draft cho client dùng chung,
-- device binding + reveal Fernet cho integration_credentials. Xem docs/api/integration-api-v1.md
-- §"Thay đổi 2026-10-14 (trước phát hành)". Forward-only, idempotent (ALTER ADD / constraint swap — KHÔNG DROP bảng/cột).

-- ── integration_clients: ownership ──────────────────────────────────────────────
ALTER TABLE integration_clients ADD COLUMN IF NOT EXISTS ownership text NOT NULL DEFAULT 'personal';

DO $$ BEGIN
    ALTER TABLE integration_clients
        ADD CONSTRAINT integration_clients_ownership_check CHECK (ownership IN ('personal', 'shared'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Backfill: client_kind='primary_tool' => personal (đã là default khi thêm cột), còn lại => shared.
UPDATE integration_clients SET ownership = 'shared'
    WHERE client_kind <> 'primary_tool' AND ownership = 'personal';

-- status: thêm 'draft' (chỉ dùng cho client shared qua wizard). Đổi CHECK (constraint tự đặt tên theo quy ước
-- Postgres <bảng>_<cột>_check cho CHECK đầu tiên trên cột — khớp constraint gốc khai trong 0001_init.sql).
ALTER TABLE integration_clients DROP CONSTRAINT IF EXISTS integration_clients_status_check;
ALTER TABLE integration_clients
    ADD CONSTRAINT integration_clients_status_check
    CHECK (status IN ('draft', 'pending_email_verification', 'active', 'suspended', 'deleted'));

-- suspended_by: thêm 'capability' (thu hồi capability:tool_integration) và 'migration' (dedupe 0008).
ALTER TABLE integration_clients DROP CONSTRAINT IF EXISTS integration_clients_suspended_by_check;
ALTER TABLE integration_clients
    ADD CONSTRAINT integration_clients_suspended_by_check
    CHECK (suspended_by IN ('account', 'admin', 'capability', 'migration'));

-- Cột mô tả/mốc thời gian của luồng wizard draft -> configured -> active.
ALTER TABLE integration_clients ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT '';
ALTER TABLE integration_clients ADD COLUMN IF NOT EXISTS configured_at timestamptz;
ALTER TABLE integration_clients ADD COLUMN IF NOT EXISTS activated_at timestamptz;
ALTER TABLE integration_clients ADD COLUMN IF NOT EXISTS activated_by text;

-- Ràng buộc ownership <-> client_kind: personal CHỈ đi với primary_tool; shared CHỈ đi với fallback_tool/service.
DO $$ BEGIN
    ALTER TABLE integration_clients
        ADD CONSTRAINT integration_clients_ownership_kind_check
        CHECK ((ownership = 'personal' AND client_kind = 'primary_tool')
            OR (ownership = 'shared' AND client_kind IN ('fallback_tool', 'service')));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Dedupe trước khi tạo unique index: mỗi owner chỉ giữ 1 client personal non-deleted CŨ NHẤT đang
-- pending_email_verification/active; các client personal khác cùng owner => suspended (suspended_by='migration'),
-- credential active của chúng => revoked (revoked_reason 'migration_0008'). Idempotent: chạy lại không đổi gì thêm
-- (vòng lặp sau chỉ còn đúng 1 ứng viên mỗi owner nên row_number() > 1 không còn hàng nào).
WITH ranked AS (
    SELECT id, row_number() OVER (PARTITION BY owner_account_id ORDER BY created_at, id) AS rn
    FROM integration_clients
    WHERE ownership = 'personal' AND status IN ('pending_email_verification', 'active')
)
UPDATE integration_clients ic
SET status = 'suspended', suspended_by = 'migration', updated_at = now()
FROM ranked r
WHERE ic.id = r.id AND r.rn > 1;

UPDATE integration_credentials cr
SET status = 'revoked', revoked_at = now(), revoke_reason = 'migration_0008'
FROM integration_clients ic
WHERE cr.client_id = ic.id AND ic.ownership = 'personal' AND ic.status = 'suspended'
    AND ic.suspended_by = 'migration' AND cr.status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS integration_clients_personal_owner_idx ON integration_clients (owner_account_id)
    WHERE ownership = 'personal' AND status IN ('pending_email_verification', 'active');

-- ── integration_credentials: reveal Fernet (cấp hộ qua wizard/provisioning) + device binding ──────────────────
ALTER TABLE integration_credentials ADD COLUMN IF NOT EXISTS reveal_ciphertext text;
ALTER TABLE integration_credentials ADD COLUMN IF NOT EXISTS reveal_state text;
DO $$ BEGIN
    ALTER TABLE integration_credentials
        ADD CONSTRAINT integration_credentials_reveal_state_check
        CHECK (reveal_state IN ('pending', 'revealed', 'expired'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
ALTER TABLE integration_credentials ADD COLUMN IF NOT EXISTS reveal_expires_at timestamptz;
ALTER TABLE integration_credentials ADD COLUMN IF NOT EXISTS revealed_at timestamptz;

ALTER TABLE integration_credentials ADD COLUMN IF NOT EXISTS bound_device_id text;
ALTER TABLE integration_credentials ADD COLUMN IF NOT EXISTS bound_installation_id text;
ALTER TABLE integration_credentials ADD COLUMN IF NOT EXISTS activated_at timestamptz;

COMMENT ON COLUMN integration_clients.ownership IS
    'personal: 1 client/tài khoản, tự provision từ capability:tool_integration. shared: admin tạo qua wizard (draft->configured->active).';
COMMENT ON COLUMN integration_credentials.reveal_state IS
    'NULL = plaintext đã trả trực tiếp lúc cấp (luồng cũ). pending/revealed/expired = cấp hộ qua wizard/provisioning, chủ phải tự "Nhận khóa".';
COMMENT ON COLUMN integration_credentials.bound_device_id IS
    'Device binding: set lần handshake đầu tiên, cố định cho tới khi xoay khoá (đổi máy = xoay khoá).';
