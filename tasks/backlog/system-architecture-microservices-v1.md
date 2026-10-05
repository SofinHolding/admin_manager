# System Architecture v1 — Manager Core + Independent Integration Services

## 0. Mục đích

Thiết kế lại Admin Manager thành một **hệ thống trung tâm (Manager Platform)** có thể tích hợp nhiều hệ thống/Tool độc lập thông qua API.

Mục tiêu chính:

- Các service hoạt động độc lập.
- Service này lỗi không làm toàn bộ hệ thống ngừng hoạt động.
- Các hệ thống không import source code của nhau.
- Các hệ thống không ghi trực tiếp database của nhau.
- Mọi giao tiếp giữa các hệ thống thông qua API contract hoặc event contract được định nghĩa rõ.
- Khi thêm Tool mới, ưu tiên chỉ cần cấp credential + tài liệu API contract, không sửa Manager chỉ để nhận diện tên Tool.
- Có thể giữ toàn bộ source trong một monorepo nhưng từng service phải build/run/deploy/restart độc lập.
- Kiến trúc phải dễ mở rộng, maintain, test và migrate từ hệ thống hiện tại.

---

## 1. Mô hình tổng thể

~~~text
                         CLIENT / USER / TOOLS
                                  |
                                  v
                        +-------------------+
                        | API Gateway / Edge |
                        +---------+---------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
              v                   v                   v
      +---------------+   +----------------+   +----------------+
      | Identity      |   | Integration    |   | Analytics      |
      | Service       |   | Service        |   | Service        |
      +---------------+   +----------------+   +----------------+
              |                   |                   |
              |                   |                   |
              +-------------------+-------------------+
                                  |
                           Manager Web/UI
                                  |
                 +----------------+----------------+
                 |                                 |
                 v                                 v
        Integration System A              Integration System B
        / Tool A                          / Tool B
        độc lập                           độc lập
~~~

Manager Platform là hệ thống chính về mặt nghiệp vụ, nhưng bên trong có thể gồm nhiều service độc lập.

---

## 2. Các bounded context / service chính

### 2.1 Identity Service

Chịu trách nhiệm duy nhất cho:

- register;
- login;
- logout;
- refresh token;
- forgot password;
- reset password;
- verify email;
- account profile;
- user roles;
- permissions;
- registration/invite keys;
- service/API credentials;
- credential revoke/rotate;
- session/security audit.

Không service khác được tự viết lại logic login/register/password nếu có thể dùng Identity Service.

Không service khác được đọc password hash trực tiếp.

### 2.2 Integration Service

Là cổng chuẩn để các Tool/hệ thống bên ngoài tích hợp với Manager.

Chịu trách nhiệm:

- Integration Client registry;
- machine credentials;
- handshake;
- platform registry;
- project/source namespace;
- resource registration/upsert;
- Tool installations/devices;
- snapshot/revision ingest;
- metrics ingest;
- job/queue contract cho Fallback Tool;
- validation;
- idempotency;
- integration audit;
- canonicalization trước khi dữ liệu đi vào analytics.

Các Tool không được ghi thẳng DB analytics.

### 2.3 Analytics Service

Chịu trách nhiệm:

- canonical analytical data;
- aggregation;
- KPI;
- traffic series;
- platform breakdown;
- user/person breakdown;
- country breakdown;
- channel/page breakdown;
- group comparison;
- source/project breakdown khi cần;
- dashboard queries;
- current snapshot/freshness.

Dashboard hiện tại sẽ dần chuyển sang đọc API từ Analytics Service.

Các yêu cầu dashboard hiện có phải được giữ:
- xem dữ liệu theo nền tảng;
- xem theo cá nhân;
- xem theo quốc gia;
- xem theo kênh/page;
- xem theo nhóm kênh;
- các KPI và biểu đồ hiện có;
- So sánh quốc gia nằm trong phần Biểu đồ riêng;
- Biểu đồ chung giữ các khung đã chốt.

### 2.4 Reward Service

Tiếp tục là domain riêng.

Chỉ giao tiếp với Manager qua API/event contract.

Không đọc/ghi bảng private của Identity/Analytics/Integration.

### 2.5 Notification Service (future)

Có thể tách khi nhu cầu đủ lớn:

- email;
- verify email;
- password reset mail;
- notification;
- webhook delivery.

V1 có thể để email adapter nằm trong Identity Service nhưng interface phải tách được.

### 2.6 Local Fallback Tool

Không phải core service trên VPS.

Là Tool/Execution Client chạy local.

Nó:
- đăng ký như Integration Client đặc biệt;
- lấy job qua API;
- chạy crawler/platform adapter local;
- push result lại Integration Service;
- không ghi trực tiếp DB Manager.

---

## 3. Hệ thống tích hợp bên ngoài

Mỗi Integration System/Tool là một hệ thống độc lập.

Ví dụ:

~~~text
Facebook Tool
TikTok Tool
YouTube Tool
Discord/Reward Tool
Fallback Local Tool
Future Tool X
~~~

Mỗi Tool có thể có source code riêng hoặc nằm cùng monorepo.

Nhưng bắt buộc:

- không import code runtime từ service khác;
- không truy cập private DB của service khác;
- không cần biết schema DB của Manager;
- không cần biết Dashboard tính toán nội bộ như thế nào;
- chỉ giao tiếp qua contract chính thức.

---

## 4. Auth cho hệ thống tích hợp

### 4.1 User authentication

Khuyến nghị dùng Manager Identity Service như authorization server.

Web/Desktop integration nên ưu tiên:

~~~text
OAuth2 / OpenID Connect
Authorization Code + PKCE
~~~

Thay vì mỗi Tool giữ password người dùng.

Flow:

~~~text
Integration App
   -> redirect/login tại Manager Identity
   -> Manager xác thực
   -> trả authorization code/token
   -> Integration App dùng token theo scope
~~~

Nếu chưa triển khai OIDC ngay, có thể giữ REST auth API hiện tại trong giai đoạn migration, nhưng mục tiêu kiến trúc vẫn là SSO/central identity.

### 4.2 Machine authentication

Tool/service-to-service không dùng password user.

Dùng:

~~~text
client_id
api_key / client_secret
scopes
contract_version
~~~

API secret:
- plaintext chỉ trả một lần;
- DB lưu hash;
- có rotate;
- có revoke;
- có last_used_at;
- có expires_at optional.

### 4.3 Role và Scope phải tách

User role:

~~~text
admin
dash_admin
viewer
...
~~~

Machine scopes:

~~~text
resources:write
snapshots:write
metrics:write
jobs:claim
jobs:complete
analytics:read
...
~~~

Không dùng user role thay cho machine scope.

---

## 5. Registration / Invite Key

Registration Key dùng để cấp quyền tạo account và policy ban đầu.

Ví dụ:

~~~text
role
max_uses
expires_at
allowed_account_features
allow_integration_registration
allowed_platforms
allowed_scopes
max_integration_clients
contract_version
~~~

Nếu user đăng ký kèm Tool:

~~~json
{
  "key": "ABCD-EFGH",
  "username": "user_a",
  "email": "user@example.com",
  "password": "********",
  "integration": {
    "create": true,
    "tool_name": "Facebook Operations Tool",
    "client_kind": "primary_tool",
    "platforms": ["facebook"]
  }
}
~~~

Server tự tạo:
- Account;
- Integration Client;
- Account <-> Client binding.

Không cần Admin/ADD mapping thủ công.

---

## 6. API Gateway

Bên ngoài chỉ nên thấy một domain/base URL.

Ví dụ:

~~~text
https://manager.example.com/api/...
~~~

Gateway route nội bộ:

~~~text
/v1/auth/*             -> identity-service
/v1/accounts/*         -> identity-service
/v1/admin/accounts/*   -> identity-service

/v1/integrations/*     -> integration-service
/v1/resources/*        -> integration-service

/v1/analytics/*        -> analytics-service

/v1/rewards/*          -> reward-service
~~~

Gateway chịu trách nhiệm:
- TLS termination;
- request id;
- rate limit cơ bản;
- routing;
- optional WAF;
- CORS chung;
- access logs.

Business authorization vẫn phải do service đích kiểm tra.

---

## 7. API Contract

Mỗi service phải có:

- OpenAPI;
- version;
- request schema;
- response schema;
- stable error.code;
- auth/scopes;
- idempotency rule;
- timeout/retry rule;
- examples;
- deprecation policy.

Không cho integration phụ thuộc vào undocumented behavior.

### Response envelope gợi ý

Success:

~~~json
{
  "ok": true,
  "data": {},
  "meta": {
    "request_id": "req_...",
    "contract_version": "2026-10-01"
  }
}
~~~

Error:

~~~json
{
  "ok": false,
  "error": {
    "code": "SCOPE_DENIED",
    "message": "Permission denied",
    "details": {}
  },
  "meta": {
    "request_id": "req_..."
  }
}
~~~

Client xử lý theo error.code, không parse message text.

---

## 8. Integration API chuẩn cho Tool

Không tạo endpoint theo tên Tool.

Không tạo:

~~~text
/facebook-itl/upload
/facebook-sible/upload
/tiktok-br/upload
~~~

Dùng generic contract:

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

Tool name chỉ là metadata.

Platform-specific rule nằm trong validator/adapter của Integration Service.

---

## 9. Resource và Project

Resource là canonical identity của Page/Channel/Profile.

Ví dụ:

~~~text
owner_account_id
+ platform
+ resource_type
+ external_id
~~~

Project/Source là namespace nghiệp vụ optional:

~~~text
itl
sible
football_br
pets_ng
project_x
~~~

Không hardcode project name.

Server tự upsert project/resource từ API.

Admin không mapping bằng tay.

---

## 10. Snapshot / Data ingestion

Tool không push dữ liệu current state bằng các document rời rạc không có transaction logic.

Dùng snapshot/revision:

~~~text
create snapshot
   ->
upload resources
   ->
upload metrics
   ->
validate
   ->
commit
~~~

Chỉ snapshot committed mới trở thành current.

Nếu Tool crash giữa chừng:
- snapshot building không được dùng;
- snapshot committed gần nhất vẫn current;
- Dashboard không bị tụt về dữ liệu thiếu/0.

---

## 11. Data flow cho Dashboard

~~~text
Tool/System
   |
   v
Integration API
   |
   v
Validation + Canonicalization
   |
   v
Committed Snapshot / Event
   |
   v
Analytics Service
   |
   v
Read Models / Aggregation
   |
   v
Manager Dashboard
~~~

Dashboard không đọc raw payload của từng Tool.

---

## 12. Giao tiếp sync và async

Không phải mọi thứ đều nên synchronous HTTP.

### Dùng synchronous API cho:

- login;
- register;
- forgot password;
- account profile;
- credential management;
- resource upsert nhỏ;
- query dashboard;
- admin operations cần response tức thì.

### Dùng async event/queue cho:

- snapshot committed;
- dữ liệu analytics lớn;
- rebuild aggregation;
- notification;
- long-running jobs;
- retry;
- background processing.

Ví dụ:

~~~text
Integration Service
   -> event: snapshot.committed
   -> Analytics Service consume
~~~

Nếu Analytics Service tạm lỗi:
- Integration vẫn nhận dữ liệu;
- snapshot không mất;
- event retry sau;
- Dashboard chỉ stale tạm thời.

Đây là fault isolation mong muốn.

---

## 13. Database ownership

Không service nào được truy cập trực tiếp private table của service khác.

Khuyến nghị:

~~~text
identity_db
integration_db
analytics_db
reward_db
~~~

Có thể dùng cùng một PostgreSQL cluster vật lý, nhưng:
- database/schema riêng;
- DB user riêng;
- permission riêng.

Ví dụ Reward cần thông tin account:
- gọi Identity API;
- hoặc consume public account event/read model.

Không JOIN trực tiếp identity.accounts từ reward DB.

---

## 14. Monorepo nhưng deploy độc lập

Cấu trúc đề xuất:

~~~text
admin_manager/
|
+-- apps/
|   +-- manager-web/
|   +-- local-fallback-tool/
|
+-- services/
|   +-- identity-service/
|   +-- integration-service/
|   +-- analytics-service/
|   +-- reward-service/
|
+-- packages/
|   +-- contracts/
|   +-- sdk-typescript/
|   +-- sdk-python/
|   +-- observability/
|
+-- infra/
|   +-- gateway/
|   +-- docker/
|   +-- migrations/
|   +-- scripts/
|
+-- docs/
|   +-- architecture/
|   +-- api/
|
+-- tests/
    +-- contract/
    +-- integration/
    +-- e2e/
~~~

Shared packages chỉ được chứa:
- DTO/schema generated;
- API clients;
- logging/trace helpers;
- generic utilities.

Không share business repository/domain model để tránh coupling.

---

## 15. Deploy độc lập

Mỗi service:
- Dockerfile hoặc run target riêng;
- health endpoint riêng;
- env riêng;
- DB migration riêng;
- log riêng;
- restart riêng;
- CI test riêng;
- deploy artifact riêng.

Ví dụ:

~~~text
identity-service      :8430
integration-service   :8431
analytics-service     :8432
reward-service        :8422
~~~

Gateway là entrypoint public.

Nếu Analytics chết:
- Identity vẫn login được;
- Integration vẫn ingest được;
- Reward vẫn chạy nếu không phụ thuộc synchronous vào Analytics.

Nếu Reward chết:
- Dashboard/Auth/Integration vẫn hoạt động.

---

## 16. Health và resilience

Mỗi service cần:

~~~text
GET /health/live
GET /health/ready
~~~

Quy tắc:
- timeout cho outbound calls;
- retry có backoff;
- circuit breaker khi phù hợp;
- không retry vô hạn;
- idempotency cho mutation;
- queue dead-letter cho job/event lỗi;
- graceful shutdown;
- request correlation id.

Service không được crash chỉ vì dependency tạm unavailable nếu có thể degrade gracefully.

---

## 17. Observability

Chuẩn chung:

- structured JSON logs;
- request_id;
- trace_id;
- service_name;
- service_version;
- client_id nếu request từ Tool;
- account_id nếu hợp lệ;
- latency;
- status/error_code.

Về sau có thể thêm OpenTelemetry.

---

## 18. Versioning

Mỗi public API có contract version.

Quy tắc:
- additive field: backward-compatible;
- breaking change: version mới;
- có deprecation window;
- contract tests cho version cũ;
- không sửa payload âm thầm.

---

## 19. Phân quyền Manager

Identity Service quản lý:

~~~text
roles
permissions
registration keys
account status
API clients
API scopes
credential status
~~~

Ví dụ user role:
~~~text
viewer
dash_admin
admin
~~~

Ví dụ Tool scope:
~~~text
resources:write
snapshots:write
metrics:write
jobs:claim
jobs:complete
~~~

---

## 20. Analytics / Dashboard requirements

Analytics Service phải support filter context thống nhất:

~~~text
date_from
date_to
owner/user
platform
country
project/source
group
~~~

Các KPI và chart phải dùng cùng filter.

Giữ yêu cầu:
- theo nền tảng;
- theo cá nhân;
- theo quốc gia;
- theo kênh/page;
- theo nhóm;
- source/project drilldown;
- data freshness.

So sánh quốc gia nằm trong Biểu đồ riêng.

Biểu đồ chung không render lại So sánh quốc gia.

---

## 21. Migration từ repo hiện tại

Không rewrite big-bang.

### Phase 0 — Freeze contract
- chốt System Architecture;
- chốt API-first Integration Contract;
- inventory dependency hiện tại.

### Phase 1 — Extract Identity
Tách:
- auth.py;
- account;
- registration key;
- email verification;
- password reset;
- token issuing.

Giữ compatibility endpoint cũ qua Gateway.

### Phase 2 — Extract Integration
Tách:
- links/resource/ops phần integration;
- Integration Client;
- API credentials;
- project/resource registry;
- snapshot ingest.

Legacy pairing vẫn chạy qua compatibility layer.

### Phase 3 — Extract Analytics
Tách:
- stats_core;
- summary;
- traffic;
- top channels;
- country/platform/user/group comparisons.

Dashboard đổi sang Analytics API.

### Phase 4 — Reward cleanup
Reward chỉ dùng public Identity/Integration APIs/events.

Loại bỏ direct access private tables.

### Phase 5 — Event bus / async pipeline
Đưa snapshot.committed -> Analytics consumer.

### Phase 6 — Retire legacy
Chỉ khi contract tests + production migration hoàn tất.

---

## 22. Anti-pattern bị cấm

DEV không được:

1. import module Python business từ service khác;
2. ghi thẳng DB service khác;
3. JOIN private table service khác;
4. hardcode tên Tool/project;
5. tạo endpoint riêng theo từng Tool khi generic API đủ;
6. dùng cùng API secret cho nhiều Tool không audit được;
7. dùng password user làm machine credential;
8. dùng JWT web dài hạn thay API key;
9. sửa payload breaking mà không version;
10. để Dashboard hiểu raw payload từng Tool;
11. để one giant admin-server process tiếp tục chứa mọi domain lâu dài;
12. tách microservice chỉ theo controller/file mà vẫn cùng process/DB ownership.

---

## 23. Definition of Done kiến trúc

Kiến trúc đạt yêu cầu khi có thể chứng minh:

### Test A — Service failure isolation

Stop analytics-service:
- login vẫn hoạt động;
- registration vẫn hoạt động;
- Integration Service vẫn nhận snapshot;
- Dashboard analytics báo unavailable/stale thay vì kéo toàn site chết.

### Test B — Add Tool mới

Tạo Tool X:
- cấp client/API credential;
- đọc OpenAPI;
- handshake;
- register resource;
- push snapshot/metrics.

Không sửa Manager source chỉ vì tên Tool X.

### Test C — Auth reuse

Tạo một Integration App mới:
- dùng Manager Identity;
- không xây user/password DB riêng;
- không đọc password hash Manager.

### Test D — DB isolation

Tắt quyền DB cross-service:
- các service vẫn hoạt động qua API/event contract.

### Test E — Independent deployment

Deploy reward-service version mới mà không restart identity/integration/analytics.

---

## 24. Nguyên tắc kiến trúc cuối cùng

~~~text
Manager Platform
= Identity + Integration + Analytics + Admin UI

Integration Systems
= các Tool/service độc lập xoay quanh Manager

API Contract
= ranh giới chính thức giữa các hệ thống

Database
= private implementation detail của từng service

Event
= cách truyền thay đổi bất đồng bộ

Gateway
= entrypoint chung

Monorepo
= nơi quản lý source chung

Service
= đơn vị deploy/restart/scale độc lập
~~~

Quy tắc đánh giá quan trọng nhất:

> Nếu thêm một Tool mới hoặc thay đổi một service mà bắt buộc phải sửa trực tiếp source code hoặc database private của service khác, thì boundary chưa đúng.

> Nếu một service crash làm các service không liên quan cùng chết, thì fault isolation chưa đạt.

