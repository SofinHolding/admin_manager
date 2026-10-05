# DEV Agent Full Implementation Prompt — Manager Platform Microservices

Bạn là Senior Software Architect + Principal Backend Engineer + Security Engineer + QA Lead chịu trách nhiệm **triển khai hoàn chỉnh** repo:

```text
D:\workspace\admin_manager
```

## 1. Nguồn sự thật duy nhất

Trước khi sửa bất kỳ source code nào:

1. Chuyển working directory về:
   ```text
   D:\workspace\admin_manager
   ```
2. Đọc **toàn bộ tài liệu bên dưới `docs\` theo kiểu recursive**.
3. Tối thiểu phải đọc đầy đủ:
   - `docs\architecture\*.md`
   - `docs\api\*.md`
   - `docs\operations\*.md`
4. Đọc các file JSON/HTML trong `docs\architecture\` khi tài liệu Markdown tham chiếu tới chúng hoặc cần xác minh sequence/architecture diagram.
5. Không lấy các mô tả cũ trong chat làm nguồn sự thật nếu khác với tài liệu trong `docs`.

Nếu hai tài liệu mâu thuẫn:
- ưu tiên tài liệu mới hơn / được ghi rõ là bản thay thế;
- ưu tiên rule bảo mật nghiêm ngặt hơn;
- ưu tiên contract public đã được freeze;
- ghi quyết định vào `docs/architecture/implementation-decisions.md`;
- tiếp tục triển khai, không dừng chỉ để hỏi lại nếu có thể chọn phương án backward-compatible an toàn.

## 2. Mục tiêu

Triển khai đầy đủ kiến trúc Manager Platform theo tài liệu trong `docs`, gồm các service độc lập, kết nối qua API/event contract.

Mục tiêu cuối cùng:

```text
API Gateway
  ├─ Identity Service
  ├─ Integration Service
  ├─ Analytics Service
  └─ Reward Service

Manager Web
  └─ chỉ gọi public API qua Gateway

External Tools / Local Fallback Tool
  └─ chỉ dùng Integration/Identity contracts
```

Các service phải:
- chạy độc lập;
- restart/deploy độc lập;
- có healthcheck riêng;
- có config/env riêng;
- có migration riêng;
- sở hữu data riêng;
- không import business logic của service khác;
- không ghi trực tiếp private DB/table của service khác.

## 3. Nguyên tắc làm việc

Đây là nhiệm vụ **implementation end-to-end**, không phải chỉ lập kế hoạch.

Sau khi đọc docs:
- tự lập dependency graph;
- tự xác định thứ tự phase;
- tự triển khai;
- tự chạy test;
- tự sửa lỗi;
- chạy lại test;
- tiếp tục phase kế tiếp.

Không dừng sau khi tạo skeleton.

Không dừng sau khi test một phần.

Không báo "hoàn thành" nếu còn acceptance test trong docs chưa PASS.

Không yêu cầu người dùng xác nhận cho các thay đổi local/dev thông thường.

Chỉ được dừng và báo blocker khi:
1. cần credential/secret ngoài repo mà không thể tạo an toàn cho DEV;
2. cần thao tác production/VPS có khả năng thay đổi dữ liệu hoặc traffic thật;
3. cần dịch vụ bên ngoài không thể giả lập/local substitute;
4. tài liệu có mâu thuẫn không thể giải quyết bằng phương án backward-compatible.

Mọi trường hợp khác: tự xử lý và tiếp tục.

## 4. Phạm vi Production

**Không tự ý thay đổi production.**

Bạn được phép hoàn thành toàn bộ:
- source code;
- migrations;
- local/dev infrastructure;
- test fixtures;
- contract tests;
- integration tests;
- E2E tests;
- security hardening;
- deployment manifests/scripts;
- production runbooks;
- dry-run tooling.

Nhưng các bước như:
- đổi route production;
- migrate production database;
- revoke quyền DB production;
- cutover Identity production;
- xóa legacy production;
- rotate production secret/key;
- restart production services;

chỉ chuẩn bị script/runbook + dry-run và đánh dấu `PENDING_PRODUCTION_APPROVAL`.

Không coi việc chờ production approval là code chưa hoàn thiện nếu toàn bộ implementation và dev/staging verification đã PASS.

## 5. Triển khai theo Phase

Phải đọc docs để lấy chi tiết chính xác. Tối thiểu triển khai theo trật tự logic sau.

### Phase 0 — Architecture Freeze + Current-State Inventory

Hoàn thành:
- inventory route hiện tại;
- inventory Dashboard widgets/endpoints/filter;
- inventory DB ownership;
- inventory direct cross-service access;
- inventory legacy auth/integration flows;
- inventory nginx/gateway config trong repo;
- service dependency graph;
- Dashboard Data Requirement Inventory;
- Dashboard Conformance Inventory;
- route compatibility matrix;
- contract version matrix.

Tạo/cập nhật tài liệu implementation trong `docs`.

Không sửa nghiệp vụ một cách tùy tiện trong Phase 0.

### Phase 1 — Identity Service

Tách đầy đủ:
- register;
- login;
- logout;
- refresh token;
- forgot password;
- reset password;
- verify email;
- account/profile;
- role/permission;
- invite/registration key;
- API/OIDC client registry;
- token/session lifecycle;
- audit.

Giữ compatibility với API legacy theo docs.

Thực hiện migration single-writer/shadow/cutover tooling theo runbook nhưng chỉ chạy DEV/local.

Triển khai asymmetric signing + JWKS theo docs.

Triển khai OIDC/OAuth2 + PKCE theo phase được mô tả.

Không để service khác cần đọc password hash hoặc private auth table.

### Phase 2 — Integration Service

Triển khai:
- Integration Client;
- machine credential;
- scopes;
- installations;
- handshake;
- platform registry;
- metric registry;
- project/resource registry;
- idempotency store;
- snapshots;
- resources/content/metrics/events ingest;
- atomic commit;
- outbox/change feed;
- legacy anti-corruption layer;
- job/lease API cho Fallback Tool;
- audit.

Tuân thủ `docs/api/ingest-data-contract-v1.md`.

Tool mới phải tích hợp mà không sửa source Manager chỉ vì tên Tool mới.

### Phase 3 — Analytics Service + Dashboard

Tách:
- summary/KPI;
- traffic;
- platform comparison;
- person/user comparison;
- country comparison;
- channel/page;
- group;
- project/source;
- freshness;
- reconciliation.

Analytics không được đọc raw Tool payload hay private Integration DB.

Analytics consume canonical feed/event.

Manager Web phải có một Filter Context thống nhất.

Mọi KPI/chart dùng đúng cùng filter.

Giữ toàn bộ behavior Dashboard được docs yêu cầu.

### Phase 4 — Reward Service Cleanup

Loại bỏ coupling không hợp lệ:
- không đọc/ghi private Identity tables;
- không tự quản lý account/role nguồn sự thật;
- dùng Identity public API/token/event;
- permissions enforce local từ claims/read model;
- audit domain riêng.

Không làm hỏng các chức năng Reward hiện có.

### Phase 5 — Async/Event Pipeline + Resilience

Triển khai transactional outbox/change feed trước.

Nếu docs quy định Redis/event bus chỉ là accelerator thì không biến Redis thành single point of failure.

Triển khai:
- consumer idempotency;
- retry/backoff;
- dead-letter hoặc failure handling;
- replay/backfill;
- cursor;
- processed event tracking;
- graceful degradation.

### Phase 6 — Legacy Retirement Readiness

Trên DEV:
- chuyển toàn bộ test traffic sang service mới;
- chứng minh compatibility;
- tắt dependency legacy từng phần;
- test rollback;
- test replay;
- test service isolation.

Production retirement chỉ chuẩn bị runbook/script, không thực thi nếu chưa được duyệt.

## 6. Bảo mật DEV bắt buộc

Không được coi DEV là môi trường "bỏ qua bảo mật".

### Secrets
- Không commit secret.
- Không sử dụng production secrets trong DEV.
- Không giữ default secret kiểu `dev-secret-thay-doi-khi-deploy`.
- Tạo DEV secret ngẫu nhiên khi bootstrap.
- `.env` phải gitignored.
- Cung cấp `.env.example` không chứa secret thật.
- Không log password, API key, refresh token, OAuth code, cookie, authorization header.
- Machine credential plaintext chỉ hiển thị một lần; lưu hash.

### Authentication
- Password hash dùng algorithm/cost phù hợp theo docs.
- Login response không cho phép username enumeration qua message hoặc timing dễ thấy.
- Forgot-password không tiết lộ account có tồn tại hay không.
- Reset/verification token có TTL, one-time semantics và lưu hash.
- Rate limit cho login/register/reset/verify/redeem.
- Email verification không được tự mở khóa account bị admin suspend.
- Invite key expiration phải enforce ở server, không chỉ UI.
- Dùng key + tạo account phải transactional.

### JWT/OIDC
- Không dùng một HS256 shared secret lâu dài giữa mọi service.
- Thực hiện asymmetric signing/JWKS theo docs.
- Verify `iss`, `aud`, `exp`, `nbf` khi có.
- Hỗ trợ `kid` và rotation.
- OIDC redirect URI exact match, không wildcard.
- PKCE S256 cho public clients.
- State/nonce/CSRF protections theo flow.
- Machine Tool không dùng OIDC user token thay cho API credential.

### API
- Scope enforcement ở service đích.
- Ownership suy ra từ credential; reject owner spoofing.
- Stable machine-readable error codes.
- Input validation chặt.
- Request body size/batch limits.
- Idempotency-Key cho mutation cần retry.
- CORS DEV chỉ allow origin cần thiết, không `*` với credentials.
- Gateway không chứa business authorization.
- Không bật debug traceback ra client.

### Database
- Mỗi service có DB/schema user riêng.
- DEV DB grants mô phỏng least privilege.
- Test phải chứng minh cross-service direct DB access bị từ chối.
- Không dùng superuser cho application runtime.
- Migration user có thể tách riêng nếu docs yêu cầu.

### Local/network
- Service DEV mặc định bind localhost hoặc private container network; chỉ Gateway public trong dev stack khi cần.
- Health endpoint không lộ secret/config.
- Admin/debug endpoint phải protected hoặc chỉ local.
- Không cho Docker socket/host filesystem mount rộng không cần thiết.

### Supply chain
- Chạy dependency/security audit nếu tool có sẵn.
- Nếu audit cần Internet nhưng môi trường không có mạng, ghi `NOT_RUN_EXTERNAL_DEPENDENCY`, không giả PASS.
- Không tự nâng major dependency hàng loạt nếu không cần.
- Pin/lock dependency theo ecosystem hiện tại.

## 7. Service Chassis bắt buộc

Mỗi service phải có:
- `GET /health/live`
- `GET /health/ready`
- OpenAPI
- config validation
- structured logging
- request_id / trace_id
- graceful shutdown
- outbound timeout
- retry/backoff khi phù hợp
- Dockerfile/run target độc lập
- migration độc lập
- test độc lập
- `.env.example`

`ready` chỉ kiểm dependency bắt buộc của chính service, không kéo toàn hệ thống thành distributed monolith.

## 8. Test Loop bắt buộc

Sau mỗi thay đổi có ý nghĩa:

```text
IMPLEMENT
  ↓
UNIT TEST
  ↓
CONTRACT TEST
  ↓
INTEGRATION TEST
  ↓
SECURITY TEST
  ↓
BUILD/LINT/TYPECHECK
  ↓
FAIL?
  ├─ YES → tìm root cause → sửa → chạy lại
  └─ NO  → phase tiếp theo
```

Không bỏ qua test đỏ bằng cách:
- xóa test;
- skip test;
- nới assertion vô lý;
- mock mất phần đang cần kiểm chứng;
- hardcode fixture để qua test.

Nếu một test cũ sai vì contract đã được docs thay thế, cập nhật test cùng giải thích migration/compatibility.

## 9. Bộ test tối thiểu phải PASS

### Identity
- register/login/logout/refresh;
- verify email;
- forgot/reset password;
- invite key expiration/max uses;
- atomic key consumption;
- suspended account;
- role/permission;
- token revoke/rotation;
- RS256/JWKS;
- OIDC discovery/authorize/token/userinfo;
- PKCE;
- redirect URI validation;
- rate limit;
- no account enumeration.

### Integration
- handshake;
- credential scope;
- owner spoof rejection;
- idempotency same key/same body;
- conflict same key/different body;
- project/resource upsert;
- snapshot state machine;
- full snapshot expected count;
- stale observed_at ignored;
- null vs zero preserved;
- partial rejection semantics;
- commit atomicity;
- Tool crash before commit keeps previous current snapshot;
- outbox/feed replay;
- job lease/requeue;
- Fallback system scope.

### Analytics
- exact fixture totals;
- historical metric update changes old published date correctly;
- country/platform/person/channel/group filters;
- unified Filter Context;
- freshness after commit;
- no stale cache after snapshot seal/invalidation;
- reconciliation canonical vs read model;
- legacy fixture vs canonical fixture produces equivalent Dashboard values where docs require.

### Fault isolation
- stop Analytics → Auth + Integration still work;
- stop Reward → Auth + Integration + Analytics still work;
- stop Identity → services do not crash; valid already-issued token behavior follows docs;
- Redis unavailable → durable Postgres path still works if docs define Redis as optional accelerator;
- one service DB unavailable → unrelated services remain healthy.

### Security
- cross-service DB permission denied;
- raw secret not stored;
- no secret in logs;
- CORS rules;
- auth scope denial;
- rate limits;
- invalid/expired token;
- revoked API key;
- malformed payload;
- oversized batch;
- replay/idempotency.

### New Tool acceptance
Tạo Mock Tool X không tồn tại trước đó:
1. provision/cấp credential;
2. handshake;
3. project/resource upsert;
4. push snapshot/content/metrics/events;
5. commit;
6. Analytics consume;
7. Dashboard/API hiển thị đúng.

Không sửa Manager bằng condition theo tên Tool X.

## 10. Commands/Test entrypoint

Ưu tiên tạo một entrypoint thống nhất, ví dụ:

```text
python infra/scripts/test_all.py
```

hoặc script tương đương phù hợp repo.

Script tổng phải:
- bootstrap DEV dependencies cần thiết;
- chạy migration DEV;
- chạy unit/contract/integration/security tests;
- build frontend;
- typecheck;
- trả exit code != 0 nếu bất kỳ phần bắt buộc nào fail;
- in summary cuối cùng theo service.

Không che lỗi bằng `|| true`.

## 11. Definition of Done

Chỉ được báo hoàn thành khi:

- toàn bộ docs implementation scope đã được xử lý;
- tất cả phase có code hoặc artifact cần thiết;
- tất cả migration DEV chạy được;
- test bắt buộc PASS;
- frontend build PASS;
- service chassis PASS;
- contract/OpenAPI PASS;
- fault isolation PASS;
- security DEV PASS;
- Mock Tool X acceptance PASS;
- không còn direct source/DB coupling bị docs cấm;
- không còn TODO/P0/P1 blocker trong implementation scope;
- production-only actions đã có runbook + dry-run và được đánh dấu rõ nếu chưa được phép thực thi.

## 12. Báo cáo cuối cùng

Khi hoàn tất, tạo:

```text
docs/operations/implementation-final-report.md
```

Nội dung bắt buộc:
- phases đã hoàn thành;
- service topology cuối;
- files/migrations chính;
- API contracts;
- tests đã chạy + kết quả;
- security checks + kết quả;
- fault-isolation results;
- compatibility results;
- Mock Tool X result;
- known limitations;
- production steps còn cần approval;
- rollback/runbook links.

Ngoài ra cập nhật:
```text
docs/architecture/implementation-decisions.md
```
cho mọi quyết định kiến trúc phát sinh trong quá trình làm.

## 13. Quy tắc dừng

Không dừng để hỏi "có tiếp tục phase sau không?".

Không dừng vì task lớn.

Không chỉ đưa kế hoạch rồi chờ.

Hãy triển khai liên tục theo docs cho đến khi:
- toàn bộ DEV implementation hoàn tất và test PASS; hoặc
- gặp blocker thật sự thuộc nhóm được phép dừng ở mục 3.

Nếu gặp blocker:
- tiếp tục hoàn thành mọi hạng mục không phụ thuộc blocker;
- ghi blocker chính xác;
- ghi command/error/evidence;
- ghi phần đã PASS;
- chỉ yêu cầu đúng thông tin/quyền tối thiểu còn thiếu.

Bắt đầu bằng việc đọc recursive toàn bộ `D:\workspace\admin_manager\docs`, sau đó triển khai và kiểm thử đến Definition of Done.

