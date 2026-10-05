# Senior Architect Master Prompt — Redesign Manager Platform into Independent Services

Bạn đang đóng vai Senior Software Architect và Lead Backend Engineer.

Mục tiêu của bạn là thiết kế lại repo Admin Manager thành một hệ thống **domain-oriented microservices trong monorepo**, trong đó Manager là hệ thống trung tâm và các hệ thống/Tool tích hợp xoay quanh Manager thông qua API contract.

Bắt buộc đọc trước khi thiết kế hoặc sửa code:

~~~text
tasks/backlog/system-architecture-microservices-v1.md
tasks/backlog/api-first-integration-contract-v1.md
~~~

## Mục tiêu nghiệp vụ

Hệ thống Manager phải cung cấp các năng lực dùng chung:
- account;
- register;
- login;
- logout;
- refresh;
- forgot/reset password;
- verify email;
- roles/permissions;
- registration/invite keys;
- API credentials/scopes;
- dashboard analytics;
- quản lý Integration Clients.

Các Tool/hệ thống tích hợp phải có thể:
- dùng Identity API/SSO của Manager;
- đăng ký machine client;
- nhận API credential;
- gửi resource/snapshot/metrics;
- đọc dữ liệu được cấp quyền;
- tích hợp mà không sửa trực tiếp source code service khác.

## Kiến trúc mục tiêu

Tối thiểu phải phân tách các bounded context:

~~~text
identity-service
integration-service
analytics-service
reward-service
manager-web
local-fallback-tool
api-gateway
~~~

Không tách service theo từng endpoint nhỏ.

Login/register/forgot/reset/roles thuộc chung Identity domain.

Resource/Tool/snapshot/ingest thuộc Integration domain.

KPI/chart/filter/aggregation thuộc Analytics domain.

## Quy tắc service isolation

Mỗi service phải:
- chạy process/container riêng;
- build riêng;
- restart riêng;
- healthcheck riêng;
- env riêng;
- migration riêng;
- sở hữu data riêng;
- không import business module của service khác;
- không ghi trực tiếp database private của service khác.

Có thể dùng cùng PostgreSQL cluster vật lý nhưng phải tách database/schema ownership + credential.

## Communication

Synchronous HTTP/API dùng cho request cần phản hồi ngay.

Async event/queue dùng cho:
- snapshot committed;
- analytics rebuild;
- background jobs;
- retry;
- notification;
- long-running task.

Không biến hệ thống thành chuỗi synchronous call dài giữa nhiều service.

## Identity

Identity Service là nguồn sự thật duy nhất về:
- account;
- password;
- user role;
- session;
- registration key;
- identity token.

Integration App không xây password DB riêng.

Ưu tiên OAuth2/OIDC + Authorization Code PKCE cho user-facing app.

Machine Tool dùng API credential riêng, không dùng user password.

User role và machine scope là hai khái niệm khác nhau.

## Integration

Không hardcode tool_name/project_name.

Không tạo endpoint theo từng Tool nếu generic contract xử lý được.

Tool mới phải tích hợp qua:

~~~text
/v1/integrations/*
~~~

Ownership lấy từ credential, không tin owner_account_id từ payload.

Resource/project được server tự upsert.

Không yêu cầu Admin/ADD mapping bằng tay.

## Analytics

Dashboard không đọc raw payload của Tool.

Pipeline bắt buộc:

~~~text
Tool
-> Integration API
-> Validation
-> Canonicalization
-> Snapshot/Event
-> Analytics Service
-> Dashboard
~~~

Analytics API phải hỗ trợ filter thống nhất:
- date;
- user;
- platform;
- country;
- project/source;
- group.

Giữ toàn bộ nghiệp vụ dashboard hiện tại.

So sánh quốc gia thuộc Biểu đồ riêng.

## Versioning

Mọi public API phải có:
- version;
- OpenAPI;
- request/response schema;
- stable error.code;
- deprecation policy;
- contract tests.

Breaking change phải tạo version mới.

## Migration

Không rewrite big-bang.

Agent phải đề xuất migration kiểu Strangler Pattern:

1. Inventory dependency hiện tại.
2. Freeze contract.
3. Extract Identity.
4. Extract Integration.
5. Extract Analytics.
6. Chuyển Reward sang public contracts.
7. Thêm async event pipeline.
8. Chuyển traffic từng bước.
9. Retire legacy cuối cùng.

Trong migration:
- endpoint cũ vẫn hoạt động;
- frontend không được chết;
- Tool cũ không bị bắt nâng cấp đồng thời;
- migration DB phải reversible hoặc có rollback strategy.

## Trước khi code

Bắt buộc trả về một Architecture Assessment gồm:

1. Current State.
2. Coupling hiện tại.
3. Target bounded contexts.
4. Service ownership matrix.
5. API dependency graph.
6. Data ownership.
7. Sync vs async communication.
8. Authentication/authorization model.
9. Migration plan.
10. Failure scenarios.
11. Testing strategy.
12. Risks.
13. Implementation order.

Không được code trước khi xác định dependency và boundary.

## Khi triển khai từng phase

Mỗi phase phải nhỏ, chạy được và backward-compatible.

Ví dụ khi Extract Identity:
- tạo service mới;
- giữ endpoint/path tương thích qua Gateway;
- chuyển DB ownership;
- thêm public key/token verification;
- thêm contract tests;
- verify old frontend vẫn login;
- sau đó mới remove auth khỏi monolith.

Không copy business logic sang hai nơi lâu dài mà không có transition plan.

## Test bắt buộc

### Fault isolation
Stop analytics:
- login vẫn hoạt động;
- integration ingest vẫn hoạt động.

Stop reward:
- auth/dashboard/integration không chết.

Stop identity:
- token đã phát còn hạn vẫn có thể được verify offline nếu thiết kế hỗ trợ;
- login mới có thể unavailable nhưng service khác không crash.

### New Tool integration
Tạo mock Tool X:
- cấp credential;
- handshake;
- push resource;
- push snapshot;
- push metrics;
- Dashboard nhận dữ liệu.

Không sửa Manager chỉ để nhận biết Tool X.

### Database isolation
Thu hồi quyền cross-schema/cross-db và đảm bảo service vẫn giao tiếp qua API/event.

### Independent deploy
Deploy/restart một service mà không restart các service không liên quan.

## Anti-pattern bị cấm

- Distributed monolith.
- Shared business DB.
- Shared mutable domain model package.
- Direct table writes giữa service.
- Hardcode Tool/project.
- Một JWT secret symmetric chia cho mọi service lâu dài.
- Synchronous call chain dài.
- Endpoint per Tool.
- Big-bang rewrite.
- Tách service chỉ theo folder nhưng vẫn cùng process.
- Service phụ thuộc vào private Python module của service khác.

## Definition of Done cuối cùng

Kiến trúc chỉ được coi là đạt khi:

1. Có thể thêm Tool mới bằng credential + API contract.
2. Không cần sửa service khác chỉ để biết Tool name.
3. Mỗi service có thể deploy/restart độc lập.
4. DB ownership rõ.
5. Service failure được cô lập.
6. Dashboard vẫn đáp ứng nghiệp vụ hiện tại.
7. Auth dùng chung toàn hệ thống.
8. API versioning + contract tests tồn tại.
9. Migration từ hệ hiện tại có lộ trình không làm gián đoạn production.
10. Không có direct source-code coupling giữa các bounded contexts.

