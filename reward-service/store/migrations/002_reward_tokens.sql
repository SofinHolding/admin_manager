-- =====================================================================
-- 002_reward_tokens.sql — nhiều token Discord / account (xoay vòng khi trả điểm)
-- Token chuyển từ reward_discord_credentials (1 account = 1 token) sang bảng riêng
-- reward_discord_tokens (1 account = N token). reward_discord_credentials chỉ còn CẤU HÌNH
-- (guild/channel/lệnh/pattern/delay). Không đụng bảng nào của admin_manager.
-- =====================================================================

CREATE TABLE IF NOT EXISTS reward_discord_tokens (
    id                bigserial PRIMARY KEY,
    account_id        text NOT NULL,                 -- FK mềm → accounts.id (chủ sở hữu)
    label             text NOT NULL DEFAULT '',
    token_ciphertext  text NOT NULL,                 -- Fernet(user token); KHÔNG BAO GIỜ trả qua API
    discord_user_id   text,                          -- từ GET /users/@me lúc xác thực
    discord_username  text,
    status            text NOT NULL DEFAULT 'unverified'
                      CHECK (status IN ('unverified','valid','invalid')),
    enabled           boolean NOT NULL DEFAULT true, -- tắt tay: token không tham gia xoay vòng
    last_error        text,
    verified_at       timestamptz,
    last_used_at      timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_reward_tokens_account ON reward_discord_tokens (account_id, id);
-- Một tài khoản Discord chỉ được thêm 1 lần cho mỗi account (tránh xoay vòng trùng chính nó)
CREATE UNIQUE INDEX IF NOT EXISTS ux_reward_tokens_acc_user
    ON reward_discord_tokens (account_id, discord_user_id) WHERE discord_user_id IS NOT NULL;

-- Ghi token nào đã gửi attempt nào (audit + gỡ lỗi)
ALTER TABLE reward_attempts ADD COLUMN IF NOT EXISTS token_id bigint;

-- Chuyển token cũ (nếu còn cột) sang bảng mới. `status` token = 'valid' CHỈ khi cấu hình cũ đang
-- 'valid'; mọi trạng thái khác → 'unverified' để buộc xác thực lại.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'reward_discord_credentials' AND column_name = 'token_ciphertext') THEN
        INSERT INTO reward_discord_tokens
            (account_id, label, token_ciphertext, discord_user_id, discord_username, status,
             last_error, verified_at)
        SELECT account_id, 'Mặc định', token_ciphertext, discord_user_id, discord_username,
               CASE WHEN status = 'valid' THEN 'valid' ELSE 'unverified' END,
               CASE WHEN status = 'valid' THEN NULL ELSE last_error END, verified_at
        FROM reward_discord_credentials
        WHERE token_ciphertext IS NOT NULL AND token_ciphertext <> ''
        ON CONFLICT DO NOTHING;

        ALTER TABLE reward_discord_credentials
            DROP COLUMN token_ciphertext,
            DROP COLUMN key_version,
            DROP COLUMN discord_user_id,
            DROP COLUMN discord_username;
    END IF;
END $$;

INSERT INTO reward_schema_migrations (version) VALUES ('002_reward_tokens')
ON CONFLICT (version) DO NOTHING;
