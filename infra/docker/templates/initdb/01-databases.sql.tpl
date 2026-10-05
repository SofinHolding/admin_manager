-- TEMPLATE. File thật được dựng vào .dev/initdb/ bởi infra/scripts/bootstrap_dev.py với mật khẩu NGẪU NHIÊN.
-- Mỗi service MỘT database và MỘT user riêng (least privilege). Một cluster Postgres vật lý, nhưng quyền tách hẳn:
-- user của service này không kết nối được database của service khác; không user runtime nào là superuser.
-- Nguồn: infra/service-registry.json (tests/arch/test_infra.py kiểm tra khớp registry).

CREATE ROLE identity_svc    LOGIN PASSWORD '${DEV_PG_IDENTITY_PASSWORD}'    NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE integration_svc LOGIN PASSWORD '${DEV_PG_INTEGRATION_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE analytics_svc   LOGIN PASSWORD '${DEV_PG_ANALYTICS_PASSWORD}'   NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE reward_svc      LOGIN PASSWORD '${DEV_PG_REWARD_PASSWORD}'      NOSUPERUSER NOCREATEDB NOCREATEROLE;

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
