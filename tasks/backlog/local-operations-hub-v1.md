# Local Fallback Tool v1 — đặc tả kiến trúc hai loại Tool + Inventory/Queue

> **Kiến trúc tích hợp chuẩn:** file này chỉ mô tả nghiệp vụ Tool A/Tool B và phải tuân theo
> `tasks/backlog/api-first-integration-contract-v1.md`. Nếu có điểm nào mâu thuẫn, API-first
> contract là nguồn sự thật ưu tiên. Không triển khai manual mapping Account/Tool/Project chỉ vì
> mô tả trong file này.

## 1. Bối cảnh và mục tiêu

Hệ thống có hai loại Tool cùng phục vụ một VPS/Dashboard nhưng khác cách lấy đầu vào.

### Tool A — Primary Tool / Tool chạy thực tế hiện có

- chạy thực tế trên máy nghiệp vụ;
- tự có cấu hình platform/resource;
- thu thập chỉ số thật;
- đồng bộ dữ liệu lên VPS;
- Dashboard đọc dữ liệu đã đồng bộ để thống kê.

### Tool B — Local Fallback Tool / Tool bổ sung cần xây

Dùng khi:
- user không có Tool A;
- platform đó chưa có Tool chạy thực tế;
- hoặc Admin muốn một máy local trung tâm chạy thay cho user.

Luồng Tool B:
1. User mở website.
2. User tạo nguồn và nhập link/username/channel/page/profile trực tiếp trên website.
3. VPS lưu inventory, owner, platform, source và trạng thái xử lý.
4. Admin/ADD đăng nhập Tool B trên máy local trung tâm.
5. Tool B kéo các resource phù hợp từ VPS về.
6. Admin/ADD xem toàn bộ inventory, filter/chọn và đưa resource vào Run Queue.
7. Phiên bản sau bổ sung platform adapter/crawler chạy thật.
8. Tool B chuẩn hóa kết quả và đẩy lại VPS.
9. VPS đưa kết quả vào cùng pipeline analytics để Dashboard thống kê.

V1 tập trung xây quy tắc, data model, inventory, source model, quyền, selection và queue. Chưa triển khai crawler/adapter thu thập chỉ số thật, nhưng kiến trúc phải sẵn sàng để Tool B trở thành một nguồn analytics đầy đủ trong bản sau.

---

## 2. Kiến trúc tổng thể

~~~text
                         +-----------------------------+
                         |            VPS              |
                         | Inventory + Sync + Analytics|
                         +-------------+---------------+
                                       |
                         canonical data contract chung
                                       |
                 +---------------------+---------------------+
                 |                                           |
                 v                                           v
       Tool A — Primary Tool                      Tool B — Local Fallback Tool
       - resource có sẵn                         - resource lấy từ Website/VPS
       - chạy tại máy nghiệp vụ                  - chạy tại máy local trung tâm
       - cào chỉ số thật                         - crawler/adapter bổ sung sau
       - push VPS                                - push VPS

User Website
   |
   +-- xem Dashboard
   +-- nếu không có Tool A -> nhập resource -> VPS -> Tool B kéo về chạy
~~~

Dashboard không được có hai bộ công thức riêng kiểu "Tool A" và "Tool B".

Khi Tool B có crawler, đầu ra của cả hai Tool phải được chuẩn hóa về cùng một canonical contract trước khi aggregate.

Có thể giữ metadata:
~~~text
collector_type = primary_tool
collector_type = fallback_local_tool
~~~

nhưng ý nghĩa metric phải giống nhau:
~~~text
resource_id
platform
date
views
interactions
comments
videos
snapshot_id
collected_at
...
~~~

collector_type dùng cho audit/debug, không làm thay đổi công thức Dashboard.

---

## 3. Vì sao Tool B nên là Desktop App

Tool B nên là desktop app chạy trên máy local trung tâm của Admin/ADD, ưu tiên Tauri hoặc Electron.

Lý do:
- về sau cần browser profile/cookie/session local;
- có thể điều khiển Chromium/AdsPower;
- cần filesystem;
- cần proxy;
- cần worker nhiều luồng;
- cần retry/resume;
- cần credential local;
- không nên để VPS trực tiếp điều khiển browser người dùng.

Website/VPS là Control Plane. Tool B là Execution Plane bổ sung.

Tool B luôn chủ động gọi VPS qua HTTPS. VPS không cần mở kết nối ngược vào máy local.

---

## 4. Các khái niệm nền tảng

### 4.1 User
Account trên Admin Manager sở hữu tài nguyên.

### 4.2 Platform
Nền tảng thật: facebook, tiktok, youtube, instagram, ...

Platform không phải nhãn tự do của Nhóm kênh.

### 4.3 Source Instance

Một namespace/nguồn nghiệp vụ độc lập trong cùng platform.

Ví dụ:
~~~text
Facebook / ITL
Facebook / Sible
Facebook / Project C
TikTok / Pet Brazil
YouTube / Football BR
~~~

ITL/Sible chỉ là ví dụ. Không được hardcode các tên này vào schema hay logic.

Một platform có thể có số Source không giới hạn.

Source dùng ID bất biến source_instance_id và metadata:
~~~text
name
platform
project_key            optional
default_country
owner_account_id
tags                   future
created_at
updated_at
~~~

### 4.4 Resource

Đối tượng thực tế:
- Facebook Page/Profile;
- TikTok account;
- YouTube channel;
- browser profile;
- resource type tương lai do adapter định nghĩa.

Resource giữ danh tính canonical vật lý:
~~~text
owner_account_id
platform
resource_type
canonical_external_id
username
url
display_name
country
~~~

### 4.5 Resource Assignment

Không nên ép một resource chỉ thuộc đúng một Source.

Tách:
~~~text
Resource = danh tính thật của channel/page/account
Assignment = resource này thuộc Source nào
~~~

Ví dụ:
~~~text
Resource: facebook/page_123
  Assignment A -> Facebook / ITL
  Assignment B -> Facebook / Project C
~~~

Lợi ích:
- thêm Source mới không phải nhân đôi resource;
- cùng một Page có thể thuộc nhiều project;
- khi xem "Mọi Source", backend có thể dedupe theo resource_id;
- khi filter một Source, dùng assignment để xác định membership.

---

## 5. Hai đường đưa resource vào hệ thống

### 5.1 Tool A sync

~~~text
origin = tool_sync
execution_mode = primary_tool
mapping_status = mapped
~~~

Tool B được phép hiển thị resource Tool A để Admin/ADD đối chiếu, nhưng không tự động queue resource đó.

### 5.2 User nhập từ website

~~~text
origin = web_manual
execution_mode = fallback_local
mapping_status = unmapped
verification_status = unverified
~~~

Đây là nhóm chính mà Tool B xử lý.

---

## 6. Quy tắc Execution Mode

Mỗi resource có đúng một execution mode chính cho cùng mục đích thu thập:
~~~text
primary_tool
fallback_local
disabled
~~~

Quy tắc:
1. Resource đã map Tool A mặc định dùng primary_tool.
2. Resource user nhập trên web, không có Tool A, dùng fallback_local nếu platform được Admin cho phép.
3. Platform chưa hỗ trợ dùng disabled.
4. Không cho một resource cùng lúc chạy Tool A và Tool B cho cùng mục đích thu thập.
5. Chuyển mode phải có audit log.
6. Nếu Tool A ngừng hoạt động và Admin chuyển sang Tool B thì phải thay đổi mode rõ ràng.

---

## 7. Website của User

Nếu user không có Tool A hoặc chưa mapping Tool, website cho phép tạo Source và nhập resource.

### Tạo Source

~~~text
Tên nguồn            Facebook ITL
Platform             Facebook
Project/Chủ đề       ITL
Country mặc định     Brazil
~~~

### Nhập resource

Hỗ trợ URL, username, channel/page/profile ID, nhập từng dòng và nhập hàng loạt.

Sau khi lưu:
~~~text
origin = web_manual
mapping_status = unmapped
execution_mode = fallback_local | disabled
verification_status = unverified
~~~

Website chỉ ghi inventory, không crawler trực tiếp trong request của user.

---

## 8. Platform Policy

Admin kiểm soát platform nào user được nhập và Tool B được phép nhận.

| Platform | Cho nhập Web | Tool B được chạy | Adapter |
|---|---:|---:|---:|
| Facebook | ON | ON | Future |
| TikTok | ON | ON | Future |
| YouTube | ON | ON | Future |
| Instagram | ON | OFF | None |

Nếu fallback_enabled = false thì resource vẫn được lưu nhưng execution_mode = disabled và không được đưa vào Run Queue.

---

## 9. Đăng nhập Tool B

Tool B dùng hệ account Admin Manager hiện có.

Cho phép:
~~~text
role = admin
role = dash_admin
~~~

Không cho:
~~~text
viewer
discord
~~~

Thêm client_type = local_fallback_tool để audit phân biệt đăng nhập từ website và Tool B.

---

## 10. Inventory Tool B kéo về

Sau login, Tool B tải inventory mà Admin/ADD được phép xem. V1 không cần tải analytics metric.

Mỗi resource tối thiểu hiển thị:
~~~text
Owner/User
Platform
Source
Project/topic
Resource display name
External ID / username / URL
Country
Origin
Mapping status
Execution mode
Verification status
Enabled/disabled
Created at
Updated at
Last operational check     future
~~~

Tool B phải hiển thị rõ resource nào của user nào.

---

## 11. Source phải mở rộng vô hạn

UI có thể group:
~~~text
User A
  Facebook
    ITL
      Page 1
      Page 2
    Sible
      Page 3
    Project C
      Page 4
  TikTok
    Pet Brazil
      @abc
~~~

Không được làm mất Source/Project khi group theo Platform.

---

## 12. Màn hình chính Tool B

### Inventory
Filter theo:
- User/Owner;
- Platform;
- Source/Project;
- Country;
- Origin;
- Mapping status;
- Execution mode;
- Verification status;
- Enabled/Disabled.

Hỗ trợ search, multi-select, select all theo filter, sort và pagination/virtualization.

### Source Tree

Hiển thị dạng User -> Platform -> Source -> Resource.

### Run Queue

Chỉ resource thỏa:
~~~text
execution_mode == fallback_local
platform_policy.fallback_enabled == true
enabled == true
~~~
mới được queue.

### Queue History
Lưu các batch đã tạo.

---

## 13. Run Queue Contract v1

Batch:
~~~text
operation_batch
  id
  created_by
  name
  platform
  created_at
  status
~~~

Item:
~~~text
operation_item
  id
  batch_id
  resource_id
  source_assignment_id
  owner_account_id
  platform
  status
  priority
  attempts
  last_error
~~~

Status:
~~~text
queued
blocked_no_adapter
ready
running       future
success       future
failed        future
cancelled
~~~

V1 chưa có crawler thật thì không được giả lập success.

---

## 14. Quy tắc Batch

Một batch nên cùng một platform.

Nếu chọn 10 Facebook + 20 TikTok thì Tool B tự tách thành hai batch.

Có thể thêm Source như metadata/filter trong batch nhưng không cần ép một batch chỉ có một Source nếu adapter vẫn tương thích.

---

## 15. Phạm vi Tool B v1

V1 chưa cào views/comments/interactions/videos/traffic.

V1 chỉ xây operational inventory + queue.

Tuy nhiên data model phải sẵn sàng cho:
~~~text
Run Queue
 -> Adapter
 -> Normalized Result
 -> Canonical Sync Contract
 -> VPS
 -> Dashboard Analytics
~~~

---

## 16. Data Model đề xuất

### resource_sources
~~~text
id
owner_account_id
name
platform
project_key
default_country
origin
created_at
updated_at
~~~

### managed_resources
~~~text
id
owner_account_id
platform
resource_type
canonical_external_id
input_ref
username
url
display_name
country
origin
mapping_status
execution_mode
verification_status
enabled
created_at
updated_at
~~~

### resource_assignments
~~~text
id
resource_id
source_instance_id
owner_account_id
enabled
created_at
updated_at
~~~

### platform_policies
~~~text
platform
web_entry_enabled
fallback_enabled
adapter_status
updated_by
updated_at
~~~

### operation_batches
~~~text
id
created_by
platform
name
status
created_at
updated_at
~~~

### operation_items
~~~text
id
batch_id
resource_id
source_assignment_id
status
priority
attempts
last_error
created_at
updated_at
~~~

### operation_audit_log
Ghi login_fallback_tool, resource_created, resource_updated, assignment_created, execution_mode_changed, batch_created, item_queued, item_cancelled, platform_policy_changed.

---

## 17. Unique Identity và chống trùng

Không dùng URL làm khóa chính.

Canonical identity:
~~~text
owner_account_id
+ platform
+ canonical_external_id
~~~

Nếu chưa resolve external ID thì dùng input_ref tạm thời và verification_status = unverified.

Source membership nằm ở resource_assignments.

Nhờ vậy cùng một canonical resource có thể thuộc nhiều Source nhưng khi xem "Mọi Source" vẫn có thể dedupe đúng.

---

## 18. Đồng bộ Inventory Tool B ↔ VPS

VPS là source of truth cho inventory do user khai báo.

Tool B có thể cache SQLite local nhưng không được coi cache local là nguồn canonical.

Luồng:
~~~text
Tool B login
  -> GET inventory revision
  -> GET resources changed since revision
  -> update local cache
~~~

V1 có thể dùng REST/manual refresh nhưng phải sẵn sàng cho incremental sync, background refresh và SSE/WebSocket.

---

## 19. API đề xuất

### User Website
~~~text
GET    /v1/resources/sources
POST   /v1/resources/sources
PATCH  /v1/resources/sources/{source_id}

GET    /v1/resources
POST   /v1/resources
POST   /v1/resources/import
PATCH  /v1/resources/{resource_id}
DELETE /v1/resources/{resource_id}
~~~

### Admin/ADD inventory
~~~text
GET /v1/ops/inventory
GET /v1/ops/inventory/revision
GET /v1/ops/users
GET /v1/ops/platforms
GET /v1/ops/sources
~~~

### Run Queue
~~~text
POST  /v1/ops/batches
GET   /v1/ops/batches
GET   /v1/ops/batches/{id}
POST  /v1/ops/batches/{id}/items
PATCH /v1/ops/items/{id}/cancel
~~~

### Platform Policy
~~~text
GET   /v1/ops/platform-policies
PATCH /v1/ops/platform-policies/{platform}
~~~

---

## 20. Permission

Viewer:
- chỉ xem/sửa Source/Resource của mình;
- không xem inventory người khác;
- không tạo batch toàn hệ thống.

dash_admin/ADD:
- xem inventory tất cả user;
- filter/chọn;
- tạo/cancel batch;
- xem queue/history.

admin:
- quyền ADD;
- sửa platform policy;
- đổi execution mode;
- xử lý duplicate/conflict.

---

## 21. UX bắt buộc

Badge execution:
~~~text
[PRIMARY TOOL]
[FALLBACK]
[DISABLED]
~~~

Badge origin:
~~~text
[Tool sync]
[Web input]
~~~

Không dùng chỉ màu; phải có text badge.

---

## 22. Future Adapter Contract

Mỗi adapter:
~~~text
platform
adapter_version
supported_resource_types
validate_resource()
prepare()
run()
normalize_result()
cleanup()
~~~

Runner core không chứa selector riêng của Facebook/TikTok/YouTube.

Adapter mới phải plug-in được mà không sửa inventory model.

---

## 23. Future Execution + Analytics

Khi crawler được thêm:
~~~text
Run Queue
   |
   v
Scheduler
   |
   v
Platform Adapter
   |
   +-- browser/profile
   +-- proxy
   +-- credential_ref
   +-- retry policy
   |
   v
Normalized Result
   |
   v
Canonical Sync Contract
   |
   v
VPS ingest/snapshot
   |
   v
Dashboard Analytics
~~~

Canonical record/snapshot phải truy được:
~~~text
owner_account_id
resource_id
platform
source/assignment context
collector_type
collected_at
snapshot_id/data_revision
~~~

Tool A và Tool B phải dùng cùng semantics cho metric.

---

## 24. Quan hệ với Nhóm kênh hiện tại

channel_groups hiện tại vẫn là metadata phục vụ grouping/filter Dashboard.

Không dùng channel_groups làm Source Instance.

Không dùng platform_tag tự do để quyết định Tool A/Tool B.

Kiến trúc:
~~~text
User
  -> Source Instance
      -> Resource Assignment
          -> Resource
              -> optional Dashboard Group
~~~

Source = namespace nghiệp vụ.
Resource = danh tính thật.
Group = cách tổ chức/so sánh giao diện.

---

## 25. Yêu cầu Dashboard đã chốt

### 25.1 So sánh quốc gia nằm trong Biểu đồ riêng

Theo yêu cầu người dùng, So sánh quốc gia phải chuyển sang phần Biểu đồ riêng.

Bố cục logic:
~~~text
Biểu đồ riêng
  - KPI theo filter hiện tại
  - Traffic theo chỉ số
  - Phân bổ nền tảng nếu phù hợp
  - Top kênh/page
  - So sánh quốc gia
~~~

So sánh quốc gia phải áp dụng cùng filter context:
~~~text
date_from
date_to
owner/user
platform
source
group
~~~

### 25.2 Biểu đồ chung chỉ còn 4 khung

~~~text
So sánh nền tảng
So sánh nhân sự
So sánh kênh
So sánh nhóm kênh
~~~

Không hiển thị So sánh quốc gia lần thứ hai trong Biểu đồ chung.

### 25.3 KPI phải theo cùng Filter Context

Các ô Tổng view, Video đã đăng, Kênh/Page đang bật phải tính đúng theo date range + country + platform + source + group + owner.

Không được hiển thị số tổng toàn hệ thống khi user đang filter.

### 25.4 Breakdown resource

Khi chọn Mọi quốc gia + Mọi nền tảng, phần Kênh/Page cần breakdown theo platform và country, có thể drill-down theo Source.

Ví dụ:
~~~text
Tổng:      113 / 124
Facebook:   48 / 52
TikTok:     36 / 40
YouTube:    29 / 32
~~~

---

## 26. Data correctness giữa Tool và VPS

Vấn đề kiểu:
~~~text
VPS: 129 / 132
Tool thật: 113 / 124
~~~
không được sửa bằng hardcode.

Cần:
1. Tool A hoặc Tool B tạo snapshot/revision.
2. Snapshot chỉ thành current khi hoàn tất thành công.
3. Resource bị xóa/inactive phải được biểu diễn rõ trong sync.
4. Dashboard trạng thái hiện tại đọc latest successful snapshot.
5. Cache invalidate khi snapshot mới commit.
6. UI hiển thị last_synced_at/data freshness.
7. Snapshot lỗi không ghi đè snapshot tốt gần nhất bằng dữ liệu 0/thiếu.

Khi Tool B có crawler, nó phải tuân thủ cùng lifecycle này.

---

## 27. Tiêu chí nghiệm thu v1

1. Viewer không có Tool A có thể tạo Source và nhập resource trên website.
2. Resource web nhập được lưu với origin=web_manual, mapping_status=unmapped.
3. Admin hoặc dash_admin login Tool B bằng account hiện tại.
4. Tool B hiển thị owner rõ cho mọi resource.
5. Không hardcode ITL/Sible; có thể tạo Source thứ 3, 4, 100 mà không sửa logic.
6. Filter được user/platform/source/country/origin/execution_mode/status.
7. Multi-select và tạo batch được.
8. Resource execution_mode khác fallback_local không được queue.
9. Platform bị Admin tắt fallback không được queue.
10. V1 chưa có adapter thật thì không giả lập success.
11. Data model/API sẵn sàng để bản sau push canonical result lên VPS.
12. So sánh quốc gia chỉ nằm trong Biểu đồ riêng.
13. Biểu đồ chung còn đúng 4 khung.
14. KPI Dashboard cùng filter context với biểu đồ.
15. Execution mode/policy/batch changes có audit log.

---

## 28. Phạm vi phát triển đề xuất

### Phase A — Server inventory foundation
- resource_sources;
- managed_resources;
- resource_assignments;
- platform_policies;
- permission;
- User API;
- Ops API.

### Phase B — Website user input
- quản lý Source;
- nhập resource đơn/hàng loạt;
- mapping/execution state;
- validation cơ bản.

### Phase C — Local Fallback Tool v1
- login Admin/ADD;
- inventory sync;
- source tree;
- filter;
- multi-select;
- run queue;
- local SQLite cache;
- queue history;
- audit.

### Phase D — Adapter framework
- adapter interface;
- worker/scheduler;
- concurrency;
- browser/profile/proxy abstraction;
- retry/resume.

### Phase E — Platform adapters
Thêm từng platform theo policy, không hardcode toàn bộ ngay từ đầu.

### Phase F — Tool B analytics integration
- normalize result;
- canonical sync contract;
- snapshot/revision;
- push VPS;
- hợp nhất với pipeline Dashboard hiện có.

### Phase G — Dashboard correctness/UI
- filter KPI thống nhất;
- platform/country/source breakdown;
- So sánh quốc gia sang Biểu đồ riêng;
- Biểu đồ chung còn 4 khung;
- data freshness;
- sửa lifecycle snapshot để tránh lệch Tool/VPS.

---

## 29. Nguyên tắc cuối cùng

~~~text
Website
= nơi user xem Dashboard
+ nơi user khai báo resource khi không có Tool A

VPS
= source of truth cho ownership
+ inventory
+ policy
+ queue
+ canonical analytics data

Tool A
= nguồn thu thập thực tế hiện có

Tool B
= nguồn thu thập bổ sung chạy tập trung trên local

Source Instance
= namespace nghiệp vụ mở rộng vô hạn

Resource
= danh tính canonical của channel/page/account

Resource Assignment
= quan hệ Resource <-> Source

Platform Adapter
= module thực thi Tool B bổ sung sau

Dashboard
= một pipeline thống kê chung nhận canonical data từ cả Tool A và Tool B
~~~

Kiến trúc này phải cho phép thêm nhiều platform, nhiều Source/Project, nhiều adapter và nhiều loại Tool mà không phải phá lại ownership, inventory, queue hoặc Dashboard.

