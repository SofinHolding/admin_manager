-- Mỗi service MỘT database và MỘT user riêng. Một cluster Postgres vật lý, nhưng quyền tách hoàn toàn:
-- user của service này không kết nối được database của service khác (kể cả đọc).
-- CHỈ DÙNG CHO DEV. Production: mật khẩu lấy từ secret manager, không commit.
-- Nguồn: infra/service-registry.json (test_infra.py kiểm tra file này khớp registry).

CREATE ROLE identity_svc    LOGIN PASSWORD 'dev_identity_change_me'    NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE integration_svc LOGIN PASSWORD 'dev_integration_change_me' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE analytics_svc   LOGIN PASSWORD 'dev_analytics_change_me'   NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE reward_svc      LOGIN PASSWORD 'dev_reward_change_me'      NOSUPERUSER NOCREATEDB NOCREATEROLE;

CREATE DATABASE identity_db    OWNER identity_svc;
CREATE DATABASE integration_db OWNER integration_svc;
CREATE DATABASE analytics_db   OWNER analytics_svc;
CREATE DATABASE reward_db      OWNER reward_svc;

-- Mặc định Postgres cho PUBLIC quyền CONNECT. Thu hồi, rồi chỉ cấp cho đúng chủ.
REVOKE ALL ON DATABASE identity_db    FROM PUBLIC;
REVOKE ALL ON DATABASE integration_db FROM PUBLIC;
REVOKE ALL ON DATABASE analytics_db   FROM PUBLIC;
REVOKE ALL ON DATABASE reward_db      FROM PUBLIC;

GRANT CONNECT ON DATABASE identity_db    TO identity_svc;
GRANT CONNECT ON DATABASE integration_db TO integration_svc;
GRANT CONNECT ON DATABASE analytics_db   TO analytics_svc;
GRANT CONNECT ON DATABASE reward_db      TO reward_svc;
