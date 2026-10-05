# EXECUTION MASTER PROMPT — Implement, Secure, Test, and Complete All Microservice Phases

Bạn đang đóng vai **Senior Software Architect + Staff Backend Engineer + DevSecOps Engineer + QA Lead**.

Nhiệm vụ của bạn không chỉ là thiết kế. Bạn phải **triển khai thực tế toàn bộ roadmap**, tự sửa lỗi, chạy test, xử lý migration, harden bảo mật môi trường DEV/TEST và tiếp tục qua từng phase cho đến khi hệ thống đạt Definition of Done.

## Tài liệu bắt buộc đọc trước khi làm

Đọc đầy đủ và coi đây là source of truth:

~~~text
tasks/backlog/system-architecture-microservices-v1.md
tasks/backlog/api-first-integration-contract-v1.md
tasks/backlog/dev-agent-system-redesign-master-prompt.md
tasks/backlog/dev-agent-api-first-master-prompt.md
~~~

Nếu các tài liệu mâu thuẫn:
1. ưu tiên system-architecture-microservices-v1.md cho system boundary;
2. ưu tiên api-first-integration-contract-v1.md cho integration/API contract;
3. không tự chọn shortcut bằng hardcode.

---

# 1. Mục tiêu cuối cùng

Triển khai hệ thống theo hướng **domain-oriented microservices trong monorepo** gồm tối thiểu:

~~~text
identity-service
integration-service
analytics-service
reward-service
api-gateway
manager-web
local-fallback-tool
~~~

Mỗi service phải:
- chạy process/container riêng;
- build riêng;
- restart riêng;
- healthcheck riêng;
- env riêng;
- migration riêng;
- log riêng;
- test riêng;
- không import business code của service khác;
- không ghi trực tiếp private DB của service khác.

Hệ thống phải vẫn giữ toàn bộ tính năng hiện tại trong quá trình migration.

---

# 2. Cách làm bắt buộc

KHÔNG chỉ đưa kế hoạch.

KHÔNG dừng sau khi viết tài liệu.

KHÔNG hỏi lại các câu đã có câu trả lời trong repo/spec.

KHÔNG chờ xác nhận giữa các phase nếu không có blocker thật sự.

Phải thực hiện theo vòng lặp:

~~~text
Analyze
-> Implement
-> Migrate
-> Test
-> Fix
-> Re-test
-> Verify acceptance criteria
-> Commit checkpoint/report
-> Move to next phase
~~~

Nếu test fail:
- đọc lỗi;
- tìm nguyên nhân;
- sửa;
- chạy lại;
- tiếp tục cho đến khi pass.

Không được bỏ test fail bằng cách disable test trừ khi test sai và phải chứng minh rõ.

---

# 3. Không được phá code hiện tại

Trước mỗi thay đổi:
- đọc file hiện tại;
- kiểm tra working tree;
- xác định user changes;
- không overwrite thay đổi không liên quan;
- migration phải additive trước;
- legacy flow tiếp tục chạy trong migration period.

Không big-bang rewrite.

Dùng **Strangler Pattern**.

---

# 4. Phase 0 — Architecture Freeze + Current-State Audit

Phải hoàn thành trước khi tách service.

## 4.1 Inventory code hiện tại

Lập bảng:

~~~text
Module/File
Current Responsibility
Target Service
Dependencies
DB Tables
Public Endpoints
Migration Risk
~~~

Ít nhất phải audit:

~~~text
admin-server/auth.py
admin-server/admin_api.py
admin-server/viewer.py
admin-server/resources.py
admin-server/ops.py
admin-server/groups.py
admin-server/links.py
admin-server/db.py
admin-server/stats_core.py
reward-service/*
local-ops-hub/*
admin-web/*
~~~

## 4.2 Dependency graph

Xác định:
- Python imports;
- HTTP dependencies;
- DB direct access;
- shared secrets;
- shared tables;
- frontend endpoints;
- Tool legacy contracts.

## 4.3 Security audit môi trường DEV

Phải tìm và xử lý các vấn đề DEV security tối thiểu:

- default JWT secret;
- hardcoded secrets;
- .env leakage;
- secret trong git;
- permissive CORS;
- HTTP plaintext ngoài localhost;
- dev server bind 0.0.0.0 không cần thiết;
- weak API keys;
- plaintext machine secrets;
- password logging;
- token logging;
- stack trace lộ thông tin;
- debug endpoints;
- SQL injection risks;
- missing validation;
- missing auth on admin/internal endpoints;
- missing rate limiting;
- insecure cookie/token storage;
- shared symmetric secret giữa nhiều service;
- unbounded payload/import;
- missing request body limits;
- missing upload limits;
- dependency vulnerabilities;
- unsafe dev test credentials;
- missing CSRF protection nếu dùng cookie auth;
- missing security headers ở Gateway;
- overly broad DB credentials;
- Redis/Postgres exposed public;
- missing localhost-only binding cho internal services trong DEV;
- missing audit trail.

Kết quả phase này phải có:
- current-state report;
- security findings;
- severity;
- fix strategy;
- files affected.

Sau audit phải **sửa ngay các lỗi Critical/High có thể sửa mà không phá compatibility**.

---

# 5. Phase 1 — Extract Identity Service

Tạo:

~~~text
services/identity-service/
~~~

Chuyển domain:

- register;
- login;
- logout;
- refresh;
- forgot password;
- reset password;
- verify email;
- account profile;
- roles;
- permissions;
- registration/invite keys;
- API client credentials;
- identity audit.

## 5.1 Auth design

Mục tiêu:
- Identity là issuer duy nhất;
- service khác verify token offline;
- không dùng một HS256 JWT_SECRET chung lâu dài.

Ưu tiên:
- Ed25519 hoặc ES256/RS256;
- Identity giữ private signing key;
- service khác chỉ có public verification key/JWKS.

Trong DEV:
- generate dev signing key tự động nếu chưa có;
- lưu ngoài git;
- permission phù hợp;
- không dùng chuỗi mặc định cố định;
- startup fail nếu production dùng default/dev key.

## 5.2 Compatibility

Gateway phải giữ endpoint cũ hoạt động nếu frontend hiện tại đang dùng:

~~~text
/v1/auth/*
/v1/admin/users/*
/v1/admin/keys/*
~~~

Không bắt frontend migrate ngay lập tức.

## 5.3 Tests

Phải có test:
- register;
- duplicate username/email;
- invalid/expired invite key;
- verify email;
- login;
- wrong password;
- suspended account;
- refresh rotation;
- logout/revoke;
- forgot/reset;
- role enforcement;
- API credential create/rotate/revoke;
- token signature verify bằng public key;
- old endpoint compatibility.

Không sang Phase 2 nếu Identity tests chưa pass.

---

# 6. Phase 2 — Extract Integration Service

Tạo:

~~~text
services/integration-service/
~~~

Chuyển/tạo domain:

- Integration Client;
- machine credentials;
- handshake;
- installation/device registry;
- platform registry;
- project/source namespace;
- managed resources;
- project/resource binding;
- snapshot ingest;
- metrics ingest;
- idempotency;
- audit;
- fallback jobs.

Không hardcode Tool/project.

## 6.1 Generic endpoints

Triển khai tối thiểu:

~~~text
POST /v1/integrations/handshake

PUT  /v1/integrations/projects/{project_key}

POST /v1/integrations/resources/batch

POST /v1/integrations/snapshots
POST /v1/integrations/snapshots/{id}/resources
POST /v1/integrations/snapshots/{id}/metrics
POST /v1/integrations/snapshots/{id}/commit

POST /v1/integrations/jobs/claim
POST /v1/integrations/jobs/{id}/complete
POST /v1/integrations/jobs/{id}/fail
~~~

## 6.2 Security

Phải:
- hash API secret;
- secret chỉ trả plaintext một lần;
- scope enforcement;
- platform enforcement;
- request id;
- idempotency;
- rate limiting;
- audit;
- owner suy ra từ credential;
- reject owner spoofing;
- payload size limits;
- batch size limits;
- stable error.code.

## 6.3 Legacy compatibility

Legacy pairing/account_links vẫn hoạt động trong transition period.

Không cho Tool mới dùng legacy flow nếu Integration API mới đã sẵn sàng.

## 6.4 Tests

Phải test:
- valid/invalid credential;
- revoked credential;
- wrong scope;
- wrong platform;
- handshake;
- project upsert;
- resource upsert;
- duplicate resource;
- idempotent retry;
- snapshot lifecycle;
- incomplete snapshot cannot commit;
- committed snapshot immutable;
- job atomic claim;
- lease expiry;
- multi-owner fallback permission;
- owner spoof rejected.

---

# 7. Phase 3 — Extract Analytics Service

Tạo:

~~~text
services/analytics-service/
~~~

Chuyển domain:

- stats_core;
- summary;
- traffic;
- top channels;
- views by country;
- platform breakdown;
- owner/person comparison;
- channel comparison;
- group comparison;
- source/project breakdown;
- freshness/current snapshot.

Dashboard không đọc raw Tool data.

## 7.1 Canonical pipeline

~~~text
Integration committed snapshot
-> event/outbox
-> Analytics consume
-> read model
-> dashboard API
~~~

Nếu chưa có message broker production-ready ngay, có thể dùng transactional outbox + polling worker ở phase đầu, nhưng contract phải cho phép thay bằng broker sau.

## 7.2 Dashboard requirements

Giữ đầy đủ:
- platform;
- individual/user;
- country;
- channel/page;
- groups;
- existing dashboard metrics;
- KPI theo cùng filter context;
- country comparison ở Biểu đồ riêng;
- Biểu đồ chung không duplicate country comparison;
- data freshness.

## 7.3 Tests

Test:
- Tool A canonical data;
- Tool B canonical data;
- combined aggregation;
- filter by date;
- platform;
- user;
- country;
- project/source;
- group;
- all filters combined;
- duplicate prevention;
- latest committed snapshot only;
- failed/incomplete snapshot ignored;
- freshness;
- exact parity với expected current dashboard formulas nơi chưa thay đổi business rule.

---

# 8. Phase 4 — Reward Service Decoupling

Reward Service không được:
- import Identity internals;
- đọc private account tables;
- update role trực tiếp trong Identity DB.

Thay bằng:
- Identity API;
- public events;
- scoped service credential.

Test:
- Identity API unavailable handling;
- retry/backoff;
- no cross-DB access;
- reward still independently deployable.

---

# 9. Phase 5 — API Gateway

Tạo:

~~~text
infra/gateway/
~~~

Gateway route:

~~~text
/v1/auth/*           -> identity
/v1/accounts/*       -> identity
/v1/admin/accounts/* -> identity

/v1/integrations/*   -> integration
/v1/resources/*      -> integration

/v1/analytics/*      -> analytics

/v1/rewards/*        -> reward
~~~

Gateway security DEV/PROD:

- TLS in production;
- DEV bind localhost by default;
- security headers;
- CORS allowlist;
- body-size limit;
- request-id;
- access log redaction;
- rate-limit baseline;
- no proxy of internal-only routes publicly;
- health endpoints separated.

---

# 10. Phase 6 — Local Fallback Tool Migration

local-fallback-tool phải:

- dùng Identity/Integration API;
- không phụ thuộc private admin-server routes lâu dài;
- claim jobs;
- lease jobs;
- complete/fail jobs;
- push normalized result;
- không tự chọn owner;
- không ghi DB.

V1 UI/inventory hiện có phải được migrate dần sang Integration contracts.

Test:
- login/auth;
- inventory;
- claim;
- lease;
- retry;
- failed adapter;
- recovery;
- push result;
- no duplicate on retry.

---

# 11. Phase 7 — Manager Web Migration

manager-web phải gọi Gateway/API contracts mới.

Không import backend internals.

Migrate từng API nhóm:
- auth;
- accounts;
- integration/admin;
- analytics.

Giữ UX hiện tại.

Test:
- login;
- register;
- forgot/reset;
- dashboard;
- filters;
- charts;
- admin accounts;
- keys;
- integration clients;
- error states;
- stale analytics state;
- one service unavailable behavior.

---

# 12. Phase 8 — Eventing / Reliability

Triển khai ít nhất:
- outbox;
- event id;
- retry;
- dedupe;
- dead-letter/error store;
- replay strategy.

Event tối thiểu:

~~~text
snapshot.committed
account.suspended
credential.revoked
resource.updated
~~~

Không cần over-engineer Kafka nếu chưa cần scale.

Có thể chọn PostgreSQL outbox + worker hoặc Redis Streams/NATS nếu phù hợp.

Phải ghi rõ tradeoff.

---

# 13. Phase 9 — DEV Security Hardening hoàn chỉnh

Môi trường DEV vẫn phải an toàn.

Bắt buộc:

## Secrets
- không commit .env;
- không commit signing keys;
- không commit API keys;
- secret scanning;
- dev secrets được generate;
- production fail-fast nếu dùng dev defaults.

## Network
- internal services bind 127.0.0.1 theo mặc định trong DEV;
- DB/Redis không expose public;
- Gateway là public entrypoint duy nhất;
- production TLS bắt buộc.

## Auth
- password bcrypt/argon2 phù hợp;
- short-lived access tokens;
- refresh revoke/rotation;
- asymmetric JWT/JWKS;
- no token/password logging;
- account lock/rate limit hợp lý.

## API
- schema validation;
- payload limits;
- batch limits;
- rate limit;
- auth on all non-public endpoints;
- stable errors;
- CORS allowlist;
- CSRF protection nếu cookie-based;
- idempotency.

## Browser/frontend
- không lưu long-lived secret trong localStorage nếu có lựa chọn an toàn hơn;
- không expose machine API secret ra frontend;
- CSP/security headers;
- XSS-sensitive rendering review.

## DB
- service-specific DB users;
- minimum privilege;
- migration user tách nếu cần;
- no cross-service writes.

## Logging
Redact:
- password;
- token;
- API key;
- reset token;
- email verify token;
- Authorization header.

## Dependencies
Chạy:
- Python dependency audit;
- npm audit;
- lockfile verification;
- fix Critical/High nếu compatible;
- ghi lại exceptions nếu chưa thể nâng.

---

# 14. Phase 10 — Testing Matrix

Phải chạy đủ:

## Unit tests
Từng service.

## Contract tests
Consumer/provider API contract.

## Integration tests
Service + DB + dependencies thật trong test environment.

## E2E
Manager Web -> Gateway -> Services.

## Security tests
- unauthorized;
- wrong role;
- wrong scope;
- expired token;
- revoked API key;
- owner spoof;
- over-limit body;
- malformed payload;
- brute force/rate limit;
- CORS;
- sensitive log scanning.

## Failure tests

### Stop analytics
Expected:
- login OK;
- register OK;
- Integration ingest OK;
- Dashboard analytics stale/unavailable rõ ràng;
- no global crash.

### Stop identity
Expected:
- existing valid token verification vẫn hoạt động offline ở services nếu token còn hạn;
- login/refresh unavailable;
- services khác không crash.

### Stop integration
Expected:
- existing dashboard historical analytics vẫn đọc được;
- auth vẫn OK;
- Reward không chết nếu không phụ thuộc sync trực tiếp.

### Stop reward
Expected:
- auth/dashboard/integration OK.

## Restart/recovery
- crash during snapshot;
- crash during job;
- event retry;
- service restart;
- DB reconnect.

---

# 15. Phase 11 — Independent Deployment

Mỗi service phải có:

~~~text
Dockerfile
.env.example
health endpoints
migration command
run command
test command
version
README
~~~

Cần có:
- docker-compose.dev.yml hoặc tương đương;
- start-all dev command;
- stop/restart riêng service;
- CI matrix theo service;
- changed-path selective test/build nếu hợp lý.

Không cần Kubernetes nếu chưa cần.

---

# 16. Phase 12 — Final Migration and Legacy Retirement

Chỉ retire legacy sau khi:

- new APIs stable;
- contract tests pass;
- manager-web migrated;
- Tool mới dùng Integration API;
- old Tool compatibility verified/migrated;
- analytics parity verified;
- production rollback plan tested.

Khi retire:
- remove unused legacy routes;
- remove direct DB coupling;
- remove shared symmetric secrets;
- document migration completion.

---

# 17. Checkpoint rule

Sau mỗi phase, Agent phải tự tạo checkpoint report:

~~~text
Phase:
Status: PASS / PARTIAL / BLOCKED

Implemented:
Tests run:
Tests passed:
Tests failed:
Security findings fixed:
Remaining risks:
Files changed:
Migration/rollback:
Next phase:
~~~

Nếu PARTIAL:
- phải tiếp tục sửa trong cùng phase nếu có thể;
- không tự chuyển phase khi acceptance criteria chưa đạt.

BLOCKED chỉ khi:
- thiếu credential/external service thật;
- thiếu dữ liệu không thể giả lập;
- cần destructive production decision;
- hoặc cần quyền mà Agent không có.

Ngay cả khi BLOCKED, phải hoàn thành tất cả phần không bị block.

---

# 18. Không được tự dừng sớm

Không được kết luận:
- "đã tạo skeleton";
- "các bước tiếp theo là...";
- "cần implement sau";
- "phase sau để future";

nếu các phase đó nằm trong phạm vi nhiệm vụ và có thể làm ngay.

Phải tiếp tục thực hiện cho đến:
- toàn bộ Definition of Done đạt;
- hoặc có blocker thật sự được chứng minh.

---

# 19. Definition of Done toàn hệ thống

Chỉ được tuyên bố COMPLETE khi:

1. Identity chạy độc lập.
2. Integration chạy độc lập.
3. Analytics chạy độc lập.
4. Reward không cross-write DB.
5. Gateway route hoạt động.
6. Manager Web dùng contracts mới.
7. Local Fallback Tool dùng contracts mới.
8. Tool mới giả lập tích hợp không sửa Manager.
9. Auth dùng chung.
10. User role và machine scope tách rõ.
11. API versioning/OpenAPI tồn tại.
12. DB ownership rõ.
13. Snapshot atomic.
14. Idempotency hoạt động.
15. Event retry/dedupe hoạt động.
16. Fault isolation tests pass.
17. Security hardening DEV pass.
18. Secret scan pass.
19. Dependency audit không còn Critical/High chưa xử lý hoặc đã có documented exception.
20. Unit/contract/integration/e2e/security tests pass.
21. Services deploy/restart độc lập.
22. Legacy retirement hoặc compatibility state được xác nhận rõ.
23. README/architecture/API/deploy docs cập nhật.
24. Không còn direct source-code coupling giữa bounded contexts.
25. Không còn direct write vào private DB service khác.

---

# 20. Câu lệnh thực thi

Bắt đầu ngay.

Không chỉ lập kế hoạch.

Hãy:
1. đọc toàn bộ spec;
2. audit repo;
3. triển khai Phase 0;
4. sửa các security issue Critical/High;
5. tiếp tục Phase 1;
6. chạy test;
7. sửa đến pass;
8. tiếp tục tuần tự qua toàn bộ phase;
9. giữ backward compatibility;
10. tạo checkpoint sau mỗi phase;
11. chỉ dừng khi Definition of Done hoàn tất hoặc có blocker thật sự.

Trong toàn bộ quá trình:
- ưu tiên correctness;
- ưu tiên backward compatibility;
- không hardcode;
- không big-bang rewrite;
- không bỏ test;
- không giả success;
- không thay đổi user code không liên quan;
- không yêu cầu xác nhận lại cho các quyết định đã được đặc tả.

