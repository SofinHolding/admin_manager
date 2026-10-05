# DEV Agent Master Prompt — API-First Integration Platform

Bạn đang phát triển repo Admin Manager.

## Mục tiêu hệ thống

Biến Admin Manager thành một nền tảng tích hợp API dùng chung cho nhiều Tool.

Yêu cầu cốt lõi:

- Tool mới không được yêu cầu Admin/ADD mapping thủ công với account.
- Tool mới không được yêu cầu sửa Dashboard chỉ vì có tên Tool mới.
- Tool mới không được có endpoint riêng nếu generic Integration API có thể xử lý.
- User có thể nhập Tool Name ngay trong luồng đăng ký tài khoản nếu Registration Key cho phép tích hợp.
- Server tự tạo Integration Client và bind với account.
- API credential xác định ownership.
- Tool chỉ cần API key + OpenAPI/contract để tích hợp.
- Dữ liệu Tool phải normalize về canonical model trước khi Dashboard sử dụng.
- Legacy flow hiện tại phải tiếp tục hoạt động trong giai đoạn migration.

Đặc tả chuẩn bắt buộc đọc trước khi code:

~~~text
tasks/backlog/api-first-integration-contract-v1.md
~~~

Nếu code hiện tại mâu thuẫn với đặc tả, không được tự chọn giải pháp nhanh bằng hardcode. Hãy báo rõ conflict và ưu tiên thiết kế additive/backward-compatible.

---

## Quy tắc bất biến

1. Không hardcode tool_name như Facebook ITL, Facebook Sible hoặc bất kỳ project cụ thể nào.
2. Tool Name chỉ là metadata/display name.
3. Platform dùng canonical key.
4. Project/Source là namespace optional do Tool/user gửi; server tự upsert, Admin không mapping bằng tay.
5. Resource là entity canonical nội bộ; Tool push resource, server tự upsert.
6. Ownership không lấy từ payload; lấy từ API credential.
7. Không cho Tool ghi trực tiếp DB.
8. Không cho Dashboard parse raw payload riêng của Tool.
9. Mọi breaking API change phải có contract version mới.
10. Mọi mutation quan trọng phải idempotent.
11. Snapshot full phải commit atomic.
12. API secret chỉ trả plaintext một lần và DB chỉ lưu hash.
13. Scope/platform phải được server enforce.
14. Migration phải additive trước; không xóa legacy account_links/documents ngay.
15. Phải có audit và machine-readable error codes.

---

## Luồng account + Tool mục tiêu

Hiện tại hệ thống có invite key + register + pairing code.

Mục tiêu mới:

~~~text
Admin tạo Registration Key có Integration Policy
    ->
User register:
    username
    email
    password
    key
    optional integration:
        tool_name
        client_kind
        platforms
    ->
Server tạo:
    account
    integration_client
    account binding
    ->
User verify email/login
    ->
User tạo API credential
    ->
Tool handshake bằng API key
    ->
Tool push project/resource/snapshot/metrics
~~~

Không tạo pairing code cho Tool mới.

Legacy pairing chỉ giữ cho Tool cũ trong migration period.

---

## Registration Key phải hỗ trợ

~~~text
role
label
max_uses
expires_at
allow_integration_registration
default_client_kind
allowed_platforms
allowed_scopes
max_integration_clients
contract_version
metadata
created_by
~~~

User/Tool không được tự xin quyền ngoài policy của key.

---

## Register API phải mở rộng backward-compatible

Existing fields:

~~~text
key
username
password
email
~~~

Thêm optional:

~~~json
{
  "integration": {
    "create": true,
    "tool_name": "Display Tool Name",
    "client_kind": "primary_tool",
    "platforms": ["facebook"]
  }
}
~~~

User chỉ dùng Dashboard thì không bắt buộc integration.

Không trả long-lived API secret trước khi verify email.

---

## Integration API namespace

Dùng generic API:

~~~text
/v1/integrations/*
~~~

Các nhóm endpoint mục tiêu:

~~~text
POST /v1/integrations/handshake

POST /v1/integrations/{client_id}/credentials
POST /v1/integrations/{client_id}/credentials/rotate
DELETE /v1/integrations/{client_id}/credentials/{key_id}

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

Không tạo endpoint theo tên Tool.

---

## Canonical resource identity

Ưu tiên:

~~~text
owner_account_id
+ platform
+ resource_type
+ external_id
~~~

Không dùng project_key trong identity.

Không dùng URL làm primary identity nếu có external_id.

Project membership là binding riêng.

---

## Canonical metric pipeline

~~~text
Tool payload
 -> validate
 -> normalize
 -> snapshot/revision
 -> canonical store
 -> dashboard aggregation
~~~

Dashboard không được đọc raw integration request.

Core metrics có registry ổn định; platform-specific metrics để trong extensions.

---

## Snapshot rule

Full snapshot:

1. create snapshot status=building;
2. upload resource/state batches;
3. upload metric batches;
4. validate expected counts/checks;
5. commit atomic;
6. chỉ snapshot committed mới trở thành current;
7. snapshot lỗi không được thay current snapshot;
8. resource missing khỏi full snapshot phải có lifecycle rõ inactive/deleted/stale.

---

## Tool B / Fallback Tool

Website manual input phải dùng cùng resource model.

Resource user nhập website:

~~~text
origin = web_manual
execution_mode = fallback_tool
~~~

Fallback Tool có system-scope phù hợp mới được claim job nhiều owner.

Owner của job do server cấp; Fallback Tool không tự chọn owner trong payload.

Job claim phải atomic + lease + retry/requeue.

---

## Security

Machine Tool dùng API credential riêng.

Không nhúng username/password user vào Tool.

Không dùng JWT web dài hạn như API key.

Header chuẩn:

~~~http
Authorization: Bearer <api-key>
X-Contract-Version: 2026-10-01
X-Request-Id: <uuid>
Idempotency-Key: <uuid>
~~~

Server phải:
- rate limit;
- validate scope;
- validate platform;
- validate contract version;
- audit request;
- revoke/rotate credential được.

---

## Response contract

Success/error phải có envelope thống nhất.

Error phải có stable error.code để Tool xử lý bằng máy.

Không bắt Tool parse chuỗi message tiếng Việt.

---

## Migration constraint

Không big-bang rewrite.

Thứ tự ưu tiên:

1. thêm schema mới;
2. thêm Integration API;
3. mở rộng registration key + register API;
4. giữ legacy hoạt động;
5. viết adapter/compatibility layer;
6. thêm contract tests;
7. migrate Tool mới;
8. sau khi ổn định mới chuyển Dashboard sang canonical store;
9. chỉ retire legacy khi đã chứng minh không còn dependency.

---

## Khi nhận một task triển khai

Trước khi sửa code, Agent phải:

1. đọc file API contract;
2. kiểm tra code hiện tại liên quan;
3. liệt kê legacy behavior cần giữ;
4. xác định schema/API nào được thêm;
5. xác định breaking risk;
6. triển khai additive;
7. thêm migration;
8. thêm tests;
9. cập nhật OpenAPI/example;
10. chạy test/build liên quan.

Không được bỏ qua bước 3-5 để code nhanh.

---

## Definition of Done

Một feature integration chỉ được coi là xong khi:

- API schema rõ;
- request/response example có;
- auth/scope rõ;
- idempotency rõ;
- error codes rõ;
- audit rõ;
- migration backward-compatible;
- tests có;
- OpenAPI cập nhật;
- không hardcode Tool name;
- không manual mapping;
- một Tool giả lập mới có thể tích hợp chỉ bằng API key + contract mà không sửa source Admin Manager.

Nếu không đạt điều cuối cùng thì thiết kế chưa đủ mở rộng.

