# API-First Integration Contract v1

## 0. Trạng thái

Đây là kiến trúc mục tiêu để Admin Manager trở thành **Integration Platform** thay vì một website phải sửa source mỗi khi xuất hiện Tool mới.

Các tên Tool, project như Facebook ITL, Facebook Sible chỉ là ví dụ. Không hardcode chúng trong backend, database hoặc frontend.

---

## 1. Mục tiêu kiến trúc

Mục tiêu cuối cùng:

~~~text
Tool mới
   |
   | chỉ cần API key + tài liệu contract
   v
Official Integration API
   |
   v
Normalize / Validate / Snapshot
   |
   v
Canonical Data Store
   |
   v
Dashboard
~~~

Khi thêm một Tool mới, quy trình bình thường KHÔNG được yêu cầu:

- Admin tự mapping Tool với account;
- ADD tự tạo Source;
- sửa Dashboard để hiểu tên Tool mới;
- thêm endpoint riêng cho từng Tool;
- thêm bảng DB riêng cho từng Tool;
- sửa công thức thống kê chỉ vì collector thay đổi.

Tool mới chỉ cần triển khai đúng API contract.

Chỉ sửa Admin Manager khi:
- contract cần version mới;
- xuất hiện metric canonical mới;
- có capability mới chưa tồn tại;
- có yêu cầu bảo mật/quyền mới;
- hoặc migration hệ thống thật sự cần thiết.

---

## 2. Nguyên tắc bắt buộc

### 2.1 API-first

Mọi Tool bên ngoài tích hợp qua một API chính thức versioned.

Không đọc/ghi trực tiếp DB.

Không phụ thuộc schema nội bộ của bảng documents.

Không tạo endpoint kiểu:

~~~text
/facebook-itl/upload
/facebook-sible/upload
/tiktok-pet/upload
~~~

Phải dùng endpoint generic:

~~~text
/v1/integrations/...
~~~

### 2.2 Không manual mapping

API key xác định Tool thuộc account nào.

Server tự tạo binding.

Không yêu cầu user tạo pairing code rồi Admin mapping lại.

### 2.3 Tool name là metadata, không phải logic

Ví dụ:

~~~text
tool_name = "Facebook ITL"
tool_name = "Facebook Sible"
tool_name = "My New TikTok Tool"
~~~

Backend không được viết:

~~~python
if tool_name == "Facebook ITL":
    ...
~~~

### 2.4 Platform là enum/canonical key

Ví dụ:

~~~text
facebook
tiktok
youtube
instagram
~~~

Nếu platform mới cần hỗ trợ thì thêm vào Platform Registry/policy, không tạo API riêng.

### 2.5 Project/Source là optional namespace

Nếu Tool có nhiều project/chủ đề, Tool gửi:

~~~text
project_key
project_name
~~~

Server tự resolve/upsert.

Không có bước Admin mapping Project bằng tay.

### 2.6 Resource là entity API nội bộ

Resource không phải một bước thao tác Admin.

Tool gửi:

~~~text
platform
resource_type
external_id
url
username
display_name
country_code
~~~

Server tự upsert resource.

---

## 3. Phân biệt 5 entity

### Account

Người dùng Dashboard.

### Integration Client

Một Tool/ứng dụng được cấp quyền API.

Ví dụ:

~~~text
Account: user_A

Integration Clients:
- "Facebook Upload Tool"
- "TikTok Collector"
~~~

### Installation

Một instance/device cụ thể của Integration Client.

Ví dụ một Tool chạy trên 2 PC thì có 2 installation khác nhau.

### Resource

Channel/Page/Profile thực tế.

### Project Binding

Optional relation giữa Resource và project/source nghiệp vụ.

Admin không mapping thủ công. Tool hoặc website tạo binding qua API.

---

## 4. Thay thế pairing hiện tại

Luồng cũ:

~~~text
Admin cấp invite key
-> User đăng ký account
-> User tạo pairing code
-> paste code vào Tool
-> Tool redeem code
-> account_links
~~~

Luồng mục tiêu:

~~~text
Admin cấp Registration Key
-> User đăng ký account
   + nhập Tool Name / Tool Kind
-> Server tạo Account
   + Integration Client
   + Account Binding
-> sau verify/login:
   Server cấp API Credential
-> Tool dùng credential gọi Integration API
~~~

Không cần pairing code trong luồng tích hợp mới.

Pairing hiện tại có thể giữ temporary cho legacy Tool trong giai đoạn migration.

---

## 5. Registration Key phải mang Integration Policy

Bảng invite/registration key không chỉ có role.

Đề xuất fields:

~~~text
key
role
label
max_uses
used_count
expires_at
status

allow_integration_registration
default_client_kind
allowed_platforms
allowed_scopes
max_integration_clients
contract_version
metadata
created_by
created_at
~~~

Ví dụ:

~~~json
{
  "role": "viewer",
  "label": "Team Brazil",
  "max_uses": 10,
  "expires_days": 30,
  "allow_integration_registration": true,
  "default_client_kind": "primary_tool",
  "allowed_platforms": ["facebook", "tiktok", "youtube"],
  "allowed_scopes": [
    "integration:handshake",
    "resources:write",
    "snapshots:write",
    "metrics:write"
  ],
  "max_integration_clients": 2,
  "contract_version": "2026-10-01"
}
~~~

Tool/user không được tự xin scope cao hơn Registration Key cho phép.

---

## 6. API đăng ký tài khoản mới

### POST /v1/auth/register

Request v2-compatible:

~~~json
{
  "key": "ABCD-EFGH",
  "username": "user_a",
  "password": "strong-password",
  "email": "user@example.com",
  "integration": {
    "create": true,
    "tool_name": "Facebook Operations Tool",
    "client_kind": "primary_tool",
    "platforms": ["facebook"]
  }
}
~~~

integration là optional để không phá user chỉ dùng Dashboard.

### Validation

Server phải:

1. validate Registration Key;
2. validate role;
3. validate allow_integration_registration;
4. intersect requested platforms với allowed_platforms;
5. không cho client tự cấp scope;
6. tạo Account;
7. tạo Integration Client nếu create=true;
8. bind client với account;
9. lưu audit.

### Response đăng ký

Không trả API secret dài hạn trước khi account được verify email.

~~~json
{
  "ok": true,
  "account_status": "pending",
  "integration": {
    "client_id": "cli_01J...",
    "tool_name": "Facebook Operations Tool",
    "client_kind": "primary_tool",
    "credential_status": "pending_email_verification"
  }
}
~~~

Sau verify/login user lấy credential.

---

## 7. API cấp credential cho Tool

### POST /v1/integrations/{client_id}/credentials

Yêu cầu:
- user đã verify email;
- client thuộc account;
- client active.

Response chỉ hiển thị secret **một lần**:

~~~json
{
  "client_id": "cli_01J...",
  "api_key": "adm_live_xxxxx.secret",
  "scopes": [
    "integration:handshake",
    "resources:write",
    "snapshots:write",
    "metrics:write"
  ],
  "contract_version": "2026-10-01"
}
~~~

DB chỉ lưu hash của secret, không lưu plaintext.

Phải hỗ trợ:
- rotate key;
- revoke key;
- last_used_at;
- expires_at optional.

---

## 8. Integration Client schema

~~~text
integration_clients
-------------------
id
owner_account_id
tool_name
client_kind
status
contract_version
allowed_platforms_json
allowed_scopes_json
created_from_registration_key
created_at
updated_at
~~~

client_kind:

~~~text
primary_tool
fallback_tool
service
~~~

Không hardcode tên sản phẩm.

---

## 9. Installation schema

~~~text
integration_installations
-------------------------
id
client_id
device_id
hostname
app_version
os
capabilities_json
status
first_seen_at
last_seen_at
last_ip
~~~

Một Tool có thể chạy trên nhiều device mà không tạo account mới.

---

## 10. Authentication của Tool

Header chuẩn:

~~~http
Authorization: Bearer adm_live_xxxxx.secret
X-Contract-Version: 2026-10-01
X-Request-Id: <uuid>
User-Agent: <tool-name>/<tool-version>
~~~

Mutation endpoint hỗ trợ:

~~~http
Idempotency-Key: <uuid>
~~~

Không dùng username/password của user trong Tool.

Không dùng JWT web dài hạn làm machine credential.

---

## 11. Handshake

### POST /v1/integrations/handshake

Request:

~~~json
{
  "installation": {
    "device_id": "device-abc",
    "hostname": "PC-BRAZIL-01",
    "os": "windows",
    "app_version": "2.4.0"
  },
  "tool": {
    "tool_name": "Facebook Operations Tool",
    "contract_version": "2026-10-01"
  },
  "platforms": ["facebook"],
  "capabilities": [
    "resources.push",
    "snapshots.push",
    "metrics.push"
  ]
}
~~~

Response:

~~~json
{
  "ok": true,
  "client_id": "cli_...",
  "installation_id": "ins_...",
  "owner_account_id": "acc_...",
  "accepted_contract_version": "2026-10-01",
  "allowed_platforms": ["facebook"],
  "allowed_scopes": [
    "resources:write",
    "snapshots:write",
    "metrics:write"
  ],
  "server_time": "2026-10-05T07:00:00Z"
}
~~~

Server không tin tool_name để xác thực ownership. Ownership lấy từ API key.

---

## 12. Project/Source tự đăng ký, không Admin mapping

Nếu Tool cần chia ITL/Sible/project khác:

### PUT /v1/integrations/projects/{project_key}

Request:

~~~json
{
  "name": "ITL",
  "platform": "facebook",
  "country_code": "BR",
  "metadata": {
    "campaign": "optional"
  }
}
~~~

Nếu project_key chưa tồn tại trong scope account/client thì server tạo.

Nếu đã tồn tại thì upsert metadata.

Không có bước ADD/Admin mapping.

Ví dụ project khác:

~~~text
itl
sible
project_c
football_br
pets_ng
~~~

Backend xử lý giống nhau.

---

## 13. Resource upsert contract

### POST /v1/integrations/resources/batch

Request:

~~~json
{
  "items": [
    {
      "platform": "facebook",
      "resource_type": "page",
      "external_id": "123456789",
      "url": "https://facebook.com/example",
      "username": "example",
      "display_name": "Example Page",
      "country_code": "BR",
      "project_key": "itl",
      "enabled": true,
      "state": "active",
      "metadata": {}
    }
  ]
}
~~~

Canonical unique key ưu tiên:

~~~text
owner_account_id
+ platform
+ resource_type
+ external_id
~~~

project_key không nằm trong canonical identity.

Project membership lưu binding riêng.

Response:

~~~json
{
  "accepted": 1,
  "created": 1,
  "updated": 0,
  "rejected": 0,
  "items": [
    {
      "external_id": "123456789",
      "resource_id": "res_...",
      "status": "created"
    }
  ]
}
~~~

---

## 14. Website manual input dùng cùng model

User không có Tool A nhập URL trực tiếp trên website.

Backend website không tạo schema khác.

Nó cũng tạo:
- managed resource;
- project binding;
- origin=web_manual;
- execution_mode=fallback_tool.

Do đó dữ liệu Tool A và dữ liệu nhập Web dùng cùng Resource model.

Tool B chỉ pull resource có execution_mode=fallback_tool.

---

## 15. API Tool B pull công việc

Tool B là Integration Client có scope hệ thống phù hợp.

### POST /v1/integrations/jobs/claim

Request:

~~~json
{
  "installation_id": "ins_fallback_01",
  "platforms": ["facebook", "tiktok"],
  "capabilities": ["resource.check", "metrics.collect"],
  "limit": 20,
  "lease_seconds": 300
}
~~~

Response:

~~~json
{
  "jobs": [
    {
      "job_id": "job_...",
      "lease_until": "2026-10-05T07:05:00Z",
      "owner_account_id": "acc_...",
      "resource": {
        "resource_id": "res_...",
        "platform": "tiktok",
        "resource_type": "channel",
        "external_id": "abc123",
        "url": "https://tiktok.com/@abc"
      },
      "project": {
        "project_key": "pets_br",
        "name": "Pet Brazil"
      }
    }
  ]
}
~~~

Claim phải atomic.

Job hết lease mà chưa complete thì có thể requeue.

---

## 16. Snapshot Contract

Không push từng document rời rạc rồi mong Dashboard suy ra trạng thái hiện tại.

Dùng snapshot/revision.

### POST /v1/integrations/snapshots

~~~json
{
  "platform": "facebook",
  "project_key": "itl",
  "snapshot_type": "full",
  "observed_at": "2026-10-05T06:55:00Z",
  "resource_count": 124
}
~~~

Response:

~~~json
{
  "snapshot_id": "snp_...",
  "status": "building"
}
~~~

### POST /v1/integrations/snapshots/{snapshot_id}/resources

Gửi batch resource/state.

### POST /v1/integrations/snapshots/{snapshot_id}/metrics

Ví dụ:

~~~json
{
  "items": [
    {
      "resource_id": "res_...",
      "observed_at": "2026-10-05T06:55:00Z",
      "metrics": {
        "views": 120034,
        "videos_posted": 4,
        "likes": 830,
        "comments": 71
      },
      "extensions": {
        "facebook": {}
      }
    }
  ]
}
~~~

### POST /v1/integrations/snapshots/{snapshot_id}/commit

Commit atomic.

Chỉ snapshot committed mới trở thành current.

Full snapshot phải cho phép server xác định resource bị missing/inactive.

Nếu collector crash giữa chừng thì snapshot cũ vẫn current.

---

## 17. Canonical Metrics

Core metric keys nên được registry/version hóa:

~~~text
views
videos_posted
likes
comments
shares
followers
interactions
active
managed
~~~

Platform-specific data đặt trong extensions:

~~~json
{
  "extensions": {
    "facebook": {
      "some_future_metric": 123
    }
  }
}
~~~

Không thêm column DB tùy tiện mỗi lần platform có field mới.

Metric nào cần trở thành Dashboard KPI chính thức thì mới promote từ extensions vào canonical registry.

---

## 18. Standard response envelope

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
    "code": "RESOURCE_VALIDATION_FAILED",
    "message": "external_id is required",
    "details": {}
  },
  "meta": {
    "request_id": "req_..."
  }
}
~~~

Tool phải branch theo error.code, không parse message text.

---

## 19. Error codes tối thiểu

~~~text
AUTH_INVALID
AUTH_REVOKED
SCOPE_DENIED
CONTRACT_VERSION_UNSUPPORTED
PLATFORM_NOT_ALLOWED
RESOURCE_VALIDATION_FAILED
RESOURCE_CONFLICT
PROJECT_VALIDATION_FAILED
SNAPSHOT_NOT_FOUND
SNAPSHOT_ALREADY_COMMITTED
SNAPSHOT_INCOMPLETE
JOB_NOT_FOUND
JOB_LEASE_EXPIRED
IDEMPOTENCY_CONFLICT
RATE_LIMITED
SERVER_ERROR
~~~

---

## 20. Versioning

Không dùng "API hiện tại" mơ hồ.

Mỗi client có:

~~~text
contract_version = 2026-10-01
~~~

Quy tắc:
- backward-compatible field additions không đổi version;
- breaking change tạo contract version mới;
- server có migration window;
- Tool cũ vẫn chạy trong thời gian hỗ trợ;
- OpenAPI/schema được lưu theo version.

Không sửa payload cũ mà khiến Tool đang chạy chết.

---

## 21. Capability negotiation

Tool handshake khai báo capabilities.

Ví dụ:

~~~text
resources.push
snapshots.push
metrics.push
jobs.pull
resource.check
browser.automation
~~~

Server trả allowed capabilities.

Tool chỉ sử dụng intersection:

~~~text
declared ∩ allowed
~~~

Nhờ vậy Tool mới có thể có chức năng khác mà không cần tạo auth system riêng.

---

## 22. Idempotency

Mọi mutation quan trọng cần Idempotency-Key:

- resource batch;
- project upsert;
- snapshot create;
- snapshot commit;
- job complete.

Nếu Tool retry do timeout, server không tạo duplicate.

---

## 23. Audit

Ghi tối thiểu:

~~~text
account_id
client_id
installation_id
api_key_id
action
resource_id optional
snapshot_id optional
request_id
ip
created_at
result
~~~

Admin có thể biết Tool nào đã gửi dữ liệu nào.

---

## 24. Không để Tool tự quyết định ownership

Payload không được có quyền thay đổi owner.

Dù Tool gửi owner_account_id giả, server phải bỏ qua hoặc reject.

owner_account_id luôn suy ra từ credential/binding.

Riêng fallback Tool có system scope mới được xử lý job nhiều owner, và owner của job do server cấp.

---

## 25. Dashboard không đọc raw Tool payload

Pipeline:

~~~text
Raw Integration Request
 -> Validation
 -> Canonicalization
 -> Snapshot/Revision
 -> Canonical Store
 -> Dashboard Aggregation
~~~

Dashboard không biết JSON riêng của từng Tool.

Đây là điều kiện để sau này thêm Tool mà không sửa Dashboard.

---

## 26. Migration từ hệ hiện tại

### Phase 1 — Additive

Giữ:
- account hiện tại;
- dashboard hiện tại;
- documents hiện tại;
- legacy account_links.

Thêm:
- integration_clients;
- integration_credentials;
- integration_installations;
- integration_projects;
- managed_resources;
- resource_project_bindings;
- snapshots;
- snapshot_metrics.

### Phase 2 — New Tool API

Tool mới chỉ dùng Integration API.

Không cho Tool mới tích hợp qua account_links legacy.

### Phase 3 — Adapter legacy

Tool cũ có thể:
- giữ flow cũ;
- hoặc viết adapter chuyển payload cũ thành canonical contract.

### Phase 4 — Dashboard canonical

Chuyển Dashboard đọc canonical snapshot store.

### Phase 5 — retire legacy

Chỉ retire documents/account_links khi tất cả Tool cũ đã migrate.

---

## 27. Thay đổi cần làm cho API đăng ký account hiện tại

RegisterBody hiện tại chỉ có:

~~~text
key
username
password
email
~~~

Mục tiêu thêm optional:

~~~text
integration.create
integration.tool_name
integration.client_kind
integration.platforms
~~~

Invite key hiện tại chỉ có role/label/max_uses/expires.

Mục tiêu thêm Integration Policy như mục 5.

Không bắt user nhập tool_name nếu tài khoản chỉ dùng Dashboard.

---

## 28. Quy tắc dành cho DEV Agent

DEV Agent phải tuân thủ:

1. Không hardcode tên Tool/project.
2. Không thêm endpoint riêng theo Tool nếu generic contract xử lý được.
3. Không yêu cầu Admin mapping thủ công nếu credential đã xác định ownership.
4. Không cho Tool ghi DB trực tiếp.
5. Không dùng URL làm canonical identity khi external_id có thể lấy được.
6. Không đưa project_key vào canonical resource identity.
7. Không để Dashboard parse payload riêng của Tool.
8. Mọi breaking API change phải version.
9. Mutation phải idempotent.
10. Snapshot phải commit atomic.
11. Credential secret lưu hash.
12. Scope/platform được server enforce.
13. Migration phải additive trước, không phá legacy ngay.
14. Viết OpenAPI + request/response example + contract tests.
15. Mọi Integration endpoint phải có error code machine-readable.

---

## 29. Definition of Done cho Integration Platform v1

Hoàn thành khi:

- Admin tạo Registration Key có Integration Policy.
- User đăng ký account và có thể nhập Tool Name ngay trong registration.
- Server tự tạo Integration Client và binding.
- User verify/login rồi tạo API credential.
- Tool handshake thành công chỉ bằng API key.
- Tool push project/resource mà không cần Admin mapping.
- Tool push full snapshot và commit.
- API idempotency hoạt động.
- Tool B claim resource web_manual.
- Audit truy được client/installation/request.
- Tool mới giả lập có thể tích hợp chỉ dựa trên OpenAPI + API key, không sửa source Admin Manager.
- Legacy flow vẫn chạy trong migration period.

---

## 30. Kiến trúc mục tiêu cuối cùng

~~~text
                    ADMIN MANAGER / VPS
              +-----------------------------+
              | Account / Registration Key  |
              | Integration Registry        |
              | Credential / Scope          |
              | Resource Registry           |
              | Project Binding             |
              | Snapshot / Canonical Metric |
              | Job Queue                   |
              +-------------+---------------+
                            |
              Official Versioned API Contract
                            |
          +-----------------+-----------------+
          |                 |                 |
          v                 v                 v
      Tool A #1         Tool A #2       Fallback Tool B
      Facebook          TikTok          Multi-platform
          |                 |                 |
          +-----------------+-----------------+
                            |
                     Canonical Data
                            |
                         Dashboard
~~~

Nguyên tắc kiểm tra kiến trúc:

> Nếu ngày mai xuất hiện một Tool mới tuân theo contract mà vẫn phải sửa Admin Manager chỉ để nhận biết tên Tool, mapping account hoặc hiểu payload riêng của Tool đó, thì kiến trúc chưa đạt API-first.

