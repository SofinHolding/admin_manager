# Test Suite Audit — Kết quả thực chạy (2026-10-14)

Môi trường: dev stack ĐANG CHẠY (GATEWAY :57965, identity :57962, integration :57963, analytics :57964,
reward :57966), cùng một Postgres DEV (`127.0.0.1:55440`) mà các bộ test cũng trỏ vào qua
`infra/scripts/devenv.py` (`--no-bootstrap`, không dựng hạ tầng mới). Mọi lệnh chạy bằng
`python infra/scripts/test_all.py --only <tên>` hoặc `python -m pytest` trực tiếp trong từng thư mục test.
Toàn bộ output dưới đây là output thật của các lệnh đã chạy, không suy đoán.

## Tóm tắt nhanh

| Bộ test | Kết quả (lần chạy ổn định nhất) | Ghi chú |
|---|---|---|
| `tests/arch` | **131 passed, 0 failed** — 14.1s | Ổn định, không cần DB |
| `tests/contract` + `pkg:contracts` | 42 passed khi chạy qua `test_all.py` (1 lần) **NHƯNG FLAKY khi chạy lại trực tiếp**: 40 passed/2 errors (deadlock), hoặc 36-38 passed + 1-2 failed assertion tuỳ lần | Root cause: tranh chấp khoá Postgres với dev stack đang chạy (xem §2) |
| `services/identity-service` (468 tests) | **KHÔNG CHẠY XONG** trong 180s lẫn 600s qua `test_all.py --only identity` | Chạy trực tiếp từng file: file không cần DB (37 tests) pass trong 5.6s; file cần DB (`test_feed_consumer.py`, 19 tests) mất **167.57s** và ra **9 failed, 9 passed, 1 error** |
| `services/integration-service` (473 tests) | **KHÔNG CHẠY XONG** trong 300s qua `test_all.py`, và trong 280s chạy trực tiếp mới đi được 38/473 test (~7.4s/test) | Không đủ thời gian hoàn tất trong một phiên hợp lý khi DB đang bị dev stack dùng chung |
| `tests/integration` (test_process_isolation) | **8 passed** — 83.97s | Chậm (~10.5s/test) nhưng xanh |
| `tests/e2e` | **1 failed, 14 passed, 16 errors** — 94.22s | 16 lỗi CÙNG MỘT nguyên nhân: fixture `world` (session-scope) timeout 30s chờ Integration nhận `account.verified`; 1 failed là vi phạm rule "không hardcode tên Tool" thật |

## 1. `tests/arch` — XANH, ổn định

```
… arch

bộ test                     pass    fail    skip     giây
arch                         131       0       0     14.1  OK

KẾT QUẢ: TẤT CẢ XANH
```

Không phụ thuộc DB/mạng, chạy nhanh và lặp lại được. Không có vấn đề.

## 2. `tests/contract` — FLAKY do tranh chấp DB với dev stack đang chạy (KHÔNG PHẢI bug cũ)

### Lần 1 (qua `test_all.py --only contract --no-bootstrap`)

```
… contract
… pkg:contracts

bộ test                     pass    fail    skip     giây
contract                      42       0       0    157.9  OK
pkg:contracts                   5       0       0      3.2  OK

KẾT QUẢ: TẤT CẢ XANH
```

### Lần 2 (chạy lại `python -m pytest tests/contract` trực tiếp, ngay sau đó)

```
...E...E..................................                               [100%]
40 passed, 2 errors in 148.12s (0:02:28)
```

2 lỗi đều là:
```
asyncpg.exceptions.DeadlockDetectedError: deadlock detected
DETAIL: Process ... waits for AccessExclusiveLock on relation ... of database 16390; blocked by process ...
HINT: See server log for query details.
```
xảy ra ở bước `TRUNCATE {TABLES} RESTART IDENTITY CASCADE` trong fixture `analytics()`
(`services/analytics-service/tests/analytics_harness.py:50`), trên `test_traffic_parity[facebook]` và
`test_country_tags_parity`.

### Lần 3 (chỉ chạy `test_analytics_stats_parity.py` một mình, lặp lại 2 lần)

- Lần A: `2 failed, 5 passed, 3 errors` — fail ở `test_views_by_country_parity` và
  `test_parity_still_holds_after_country_alias_normalization` (dữ liệu `totals`/`countries` bị **rỗng**
  thay vì có `Brazil/Chile/Khác/Peru/br`), cộng 3 deadlock lúc TRUNCATE.
- Lần B: `1 failed, 6 passed, 3 errors` — chỉ còn `test_views_by_country_parity` fail, còn
  `test_parity_still_holds...` thì **pass**.

### Lần 4 — cô lập đúng 2 test bị nghi từ audit trước (lặp 3 lần liên tiếp)

```
=== RUN 1 === .. 2 passed in 8.30s
=== RUN 2 === .. 2 passed in 11.58s
=== RUN 3 === .. 2 passed in 8.33s
```
→ `test_channel_breakdown_parity` và `test_parity_still_holds_after_country_alias_normalization`
**pass ổn định 3/3 lần khi chạy cô lập**. Bug "country alias seed-resurrection" được ghi nhận đã sửa
(khớp với memory: "Recently fixed country alias seed-resurrection bug").

### Lần 5 — `test_summary_parity_with_documented_difference_for_managed_channels` một mình

- Lần đầu (ngay sau 1 loạt chạy dồn dập khác): **FAIL** — `assert 0 == 682` (total_views).
- 3 lần chạy lại ngay sau: **pass** cả 3 lần (`1 passed in ~4.3s` mỗi lần).

### Kết luận §2

Không có lỗi logic ổn định lặp lại trong `test_analytics_stats_parity.py`. Toàn bộ sai lệch quan sát được
(deadlock TRUNCATE, `total_views=0`, `countries=[]`) đều **biến mất khi chạy lại cùng test trong điều kiện
ít tải hơn**, và bằng chứng trực tiếp trên `pg_stat_activity` tại thời điểm hang của bộ identity
(xem §3) cho thấy có phiên khác (không phải phiên test) đang giữ khoá `transactionid` trên
`oidc_cursor ... FOR UPDATE`. Dev stack đang chạy (identity/integration/analytics) dùng **chung Postgres**
với test harness; test harness tự `TRUNCATE`/ghi dữ liệu mẫu vào các bảng mà service thật cũng đang
polling/ghi — gây deadlock và/hoặc cửa sổ đọc giữa chừng (dữ liệu test chưa kịp "ổn định" khi query so
sánh chạy). Đây là **vấn đề cô lập môi trường test (thiếu DB riêng cho test khi dev stack đang chạy)**,
không phải regression của 2 test được nêu trong audit tài liệu trước.

## 3. `services/identity-service` — KHÔNG hoàn tất trong giới hạn thời gian hợp lý khi chạy cùng dev stack

- `python infra/scripts/test_all.py --only identity --no-bootstrap` treo **không có output nào** sau cả
  180s và 600s (2 lần thử, cả 2 đều bị kill do quá hạn, không in được cả dòng tổng kết).
- Thu hẹp phạm vi: collect (không chạy) 468 test thành công trong 0.36s → vấn đề không phải ở
  import/collection.
- Các file KHÔNG cần DB (`test_units.py`, `test_config.py`, `test_health.py`, `test_logredact.py`, 37
  test) chạy xong **5.65s, 37 passed, 0 failed** → phần code thuần (hash, JWT, rate-limit, settings) ổn.
- File cần DB nặng nhất, `test_feed_consumer.py` (19 test), chạy một mình mất **167.57s** (tức hơn
  8.8s/test, trong khi logic thực tế của các test này là async, không lẽ chậm vậy nếu không có tranh
  chấp) và ra:
  ```
  pytest: 9 failed, 9 passed, 1 error in 167.57s (0:02:47)
  ```
  - 1 lỗi: `DeadlockDetectedError` ngay lúc setup fixture `db` (`TRUNCATE {_TABLES} ...`) —
    `Process ... waits for AccessExclusiveLock ...; blocked by process ...`.
  - 9 test fail đều theo một khuôn mẫu: `stats["applied"]` hoặc `stats["dead"]` bằng **0** trong khi kỳ
    vọng >0 (ví dụ `test_provisioned_and_failed_events_update_status`, `test_pagination_pulls_every_page`
    kỳ vọng 230 nhưng được 224, `test_malformed_and_mismatched_events_are_dead_lettered` kỳ vọng
    `dead==3` nhưng được 0, `test_run_forever_survives_dead_source_and_resumes` kỳ vọng status
    chuyển `provisioned` nhưng vẫn `pending`).
- Bằng chứng trực tiếp từ Postgres tại thời điểm chạy (`pg_stat_activity` trên DB `identity_db`,
  21 kết nối đang mở, `max_connections=100` — chưa cạn pool nhưng có tranh chấp khoá thật):
  ```
  pid 113900 state=active wait_event_type=Lock wait_event=transactionid
      query: SELECT seq FROM oidc_cursor WHERE name = $1 FOR UPDATE
  pid 114986 state=active wait_event_type=IO wait_event=WALSync query: COMMIT;
  ```
  5 giây sau, cả hai phiên đã chuyển `idle` — xác nhận đây là **tranh chấp khoá tạm thời (lock
  contention), không phải deadlock vĩnh viễn**, nhưng đủ để làm từng test chậm đáng kể và/hoặc đọc dữ
  liệu giữa chừng khi một phiên khác (nhiều khả năng background feed-consumer loop của chính
  identity-service dev stack đang polling `oidc_cursor`) đang giữ `FOR UPDATE` trên cùng bảng mà test
  `TRUNCATE`/ghi.

**Kết luận**: `services/identity-service` test suite (DB-dependent phần) KHÔNG đáng tin khi chạy song
song với dev stack dùng chung Postgres: vừa deadlock lúc TRUNCATE, vừa có sai lệch kết quả do dữ liệu bị
"xen" bởi tiến trình khác. Không rút ra được kết luận PASS/FAIL thật sự cho các test trong
`test_feed_consumer.py` từ lần chạy này — cần DB test cách ly (schema/instance riêng) để có kết quả tin
được.

## 4. `services/integration-service` — tương tự, KHÔNG hoàn tất

- `test_all.py --only "svc:integration" --no-bootstrap`: timeout sau 300s, không có output
  (`… svc:integration-service` rồi im lặng).
- Chạy trực tiếp `python -m pytest` trong thư mục: collect được **473 test** (0.64s), nhưng sau
  **280s chỉ xong 38 test** (`........` × ~4, tốc độ ~7.4s/test) — với tốc độ này cần ước lượng
  ~58 phút để hết 473 test, vượt xa mọi ngưỡng CI hợp lý.
- Đây là CÙNG nguyên nhân với §2/§3: integration-service test harness cũng TRUNCATE/claim trên cùng
  Postgres mà dev stack integration-service thật đang chạy, gây tranh chấp khoá liên tục.

**Không thể kết luận PASS/FAIL cho integration-service test suite trong điều kiện hiện tại** (dev stack
đang chạy, chia sẻ DB). Cần dừng dev stack hoặc trỏ test vào DB cách ly để đo được số liệu thật.

## 5. `tests/integration` (test_process_isolation.py) — XANH nhưng chậm

```
........                                                                 [100%]
8 passed in 83.97s (0:01:23)
```
Pass 8/8, nhưng trung bình ~10.5s/test — chậm bất thường so với một test "process isolation" thuần,
khả năng cũng chịu ảnh hưởng tranh chấp tài nguyên từ dev stack đang chạy, dù không đủ để gây fail.

## 6. `tests/e2e` — 1 FAIL thật + 16 ERROR cùng một nguyên nhân gốc

```
pytest: 1 failed, 14 passed, 16 errors in 94.22s (0:01:34)
```

### 6.1. FAIL thật: `test_manager_source_has_no_knowledge_of_the_new_tool`

```
AssertionError: Manager biết tên Tool mới: ['apps\\manager-web\\src\\routes\\DashboardPage.test.tsx']
```
Test này quét toàn bộ `services/`, `apps/manager-web/src`, `packages/`, `infra/` tìm chuỗi
`"toolx_social"` (= `TOOL_PLATFORM` trong `tests/e2e/conftest.py:142`, tên nền tảng của Mock Tool X dùng
làm fixture e2e). File `apps/manager-web/src/routes/DashboardPage.test.tsx` dùng đúng chuỗi
`"toolx_social"` làm **tên nền tảng giả định trong test frontend** (dòng 54, 265-271) — trùng ký tự với
tên platform thật của Mock Tool X bên e2e. Đây LÀ một vi phạm thật theo định nghĩa của rule test (luật 4:
không hardcode tên Tool trong mã nguồn Manager) dù bản chất là trùng tên ngẫu nhiên giữa 2 bộ test độc
lập, không phải Manager thực sự "biết" về Tool X. Cần đổi platform giả định trong
`DashboardPage.test.tsx` sang tên khác (vd. `"demo_social_2"`) để tránh đụng namespace với fixture e2e.

### 6.2. 16 ERROR: cùng 1 nguyên nhân — fixture `world` (session-scope) timeout

Tất cả 16 lỗi đều dừng ở cùng một dòng, cùng một thông điệp:
```
tests\e2e\conftest.py:212: in world
    wait_for(issue, timeout=30, what="Integration nhận email_verified của owner")
...
AssertionError: quá hạn chờ Integration nhận email_verified của owner (30s); giá trị cuối: None
```
Luồng: fixture `world` (session-scope, `tests/e2e/conftest.py`) tạo admin, tạo invite key, đăng ký owner
qua API công khai, rồi chờ (poll 0.5s, tối đa 30s) Integration service "nhận" được sự kiện
`account.verified` của Identity (qua feed consumer định kỳ) để có thể issue credential
(`POST /v1/integrations/clients/{cid}/credentials` trả `201`). Sau 30s vẫn nhận `403
EMAIL_NOT_VERIFIED` → timeout, fixture ném `AssertionError`, và **toàn bộ 16 test còn lại trong 2 file
`test_mock_tool_x.py` + `test_z_fault_isolation.py` đều ERROR theo (session fixture hỏng một lần, hỏng
cho mọi test dùng nó)**.

Vì đây là lỗi 1 LẦN duy nhất ở setup (session fixture chỉ chạy 1 lần), không thể phân biệt được đây là:
(a) bug thật trong provisioning consumer của integration-service (không kéo kịp `account.verified`
trong 30s), hay (b) hệ quả của dev stack integration-service/identity-service đang chạy sẵn khiến feed
consumer riêng của bộ test `world` (tạo từ `stack` fixture, tiến trình MỚI trên port khác) bị tranh chấp
tài nguyên/CPU/DB với dev stack đang chạy nền, làm nó không kịp poll trong 30s. Không đủ bằng chứng để
kết luận root cause chính xác trong phạm vi audit này — cần chạy lại `tests/e2e` khi KHÔNG có dev stack
nền chạy song song để phân biệt.

14 test PASS trước khi fixture `world` được gọi lần đầu (các test không phụ thuộc `world`, ví dụ test
kiến trúc/độc lập khác trong cùng session) — không liệt kê chi tiết vì không phải trọng tâm audit.

## 7. Độ phủ test cho tính năng MỚI (sau audit tài liệu 2026-10-08)

| Tính năng mới | Có test không? | Bằng chứng |
|---|---|---|
| `GET /v1/integrations/resources` (machine, scope `jobs:claim`) | **KHÔNG CÓ test nào** | `grep` toàn bộ `services/integration-service/tests` cho `list_own_resources`/`jobs:claim` chỉ ra các match liên quan đến CẤP SCOPE (`test_clients_credentials.py`, `test_jobs.py`, `test_units.py`), không có lệnh `GET` nào gọi `/v1/integrations/resources` (phân biệt với route cũ `/v1/resources` của legacy ACL, dùng biến `RES` import từ `test_legacy_acl.py` — route KHÁC) |
| `GET /v1/integrations/resource-owners` | **KHÔNG CÓ test nào** | Không tìm thấy bất kỳ lệnh gọi `resource-owners` nào trong toàn bộ `tests/` |
| `account.permission_changed` consumer trong `integration-service` (`provisioning/handlers.py:_permission_changed` — tự tạo `primary_tool` client khi được cấp `capability:tool_integration`) | **KHÔNG CÓ test nào trong integration-service** | `test_provisioning.py` có 20+ test cho `account.registered/verified/status_changed/deleted`, KHÔNG có test nào `fake_feed.add("account.permission_changed", ...)`. (Lưu ý: `account.permission_changed` CÓ test ở phía identity-service — nơi phát event — và ở reward-service — nơi tiêu thụ cho `reward:access`; chỉ riêng integration-service, nơi vừa thêm handler `_permission_changed` mới, là chưa có test.) |
| `capability:tool_integration` enforcement qua `require_tool_integration` (`services/integration-service/src/integration_service/deps.py:42`) | **CÓ dùng trong route** (`clients/router.py: create_client`, `credentials/router.py: issue_credential*`, `rotate_credential`) nhưng KHÔNG tìm thấy test riêng kiểm tra "user KHÔNG có `capability:tool_integration` thì bị 403" | `grep "require_tool_integration"` chỉ ra nơi dùng (router), không có test nào trong `tests/` assert 403 khi thiếu capability này cho các route trên |

## 8. Đánh giá tổng thể sức khoẻ test suite

- **`tests/arch` (131 test)**: Đáng tin cậy 100%, xanh ổn định, không cần hạ tầng.
- **`tests/contract` (42 test)**: Logic ĐÚNG khi cô lập (đã xác nhận 2 test từng fail trong audit cũ nay
  pass ổn định 3/3 lần), nhưng **không đáng tin khi chạy đồng thời với dev stack** do tranh chấp
  Postgres dùng chung — flaky giữa các lần chạy (có lúc 42/0, có lúc 40 passed + 2 deadlock-error).
- **`services/identity-service`, `services/integration-service`**: **KHÔNG xác minh được PASS/FAIL thật
  sự** trong điều kiện audit này — cả hai đều hoặc treo quá thời gian cho phép (300-600s), hoặc chạy
  chậm bất thường (identity: ~8.8s/test cho file DB-nặng; integration: ~7.4s/test, ước ~58 phút cho hết
  473 test) kèm deadlock/sai lệch dữ liệu khi cô lập một phần. **Nghi vấn hợp lý nhất, có bằng chứng trực
  tiếp từ `pg_stat_activity`: dev stack identity-service đang chạy giữ khoá `FOR UPDATE` trên
  `oidc_cursor` (vòng lặp feed-consumer nền) đụng độ với test harness TRUNCATE cùng bảng.**
- **`tests/integration`**: xanh (8/8) nhưng chậm gấp nhiều lần bình thường — cùng nghi vấn tranh chấp
  tài nguyên.
- **`tests/e2e`**: 1 fail thật (đáng sửa, nhỏ — đổi tên platform giả trong file test frontend), nhưng
  16/17 test còn lại không chạy được do 1 fixture session-scope timeout ở bước chờ provisioning — chưa
  xác định được có phải bug thật của consumer hay chỉ do tranh chấp tài nguyên với dev stack nền.
- **Độ phủ test tính năng mới**: 3/4 khía cạnh được hỏi có lỗ hổng rõ ràng — `GET /resources`,
  `GET /resource-owners`, và `account.permission_changed` trong integration-service đều **chưa có test
  nào**. `require_tool_integration` có dùng nhưng thiếu test 403 riêng.

### Khuyến nghị hành động (không nằm trong phạm vi audit, chỉ ghi nhận)

1. Test suite của `identity-service`/`integration-service`/`contract` cần DB test CÁCH LY khỏi DB mà dev
   stack dùng (schema riêng, hoặc dừng dev stack trước khi chạy full test) để có kết quả tin cậy — hiện
   `test_all.py --no-bootstrap` đang trỏ cùng DSN với dev stack đang chạy.
2. Thêm test cho `GET /v1/integrations/resources`, `GET /v1/integrations/resource-owners`,
   `account.permission_changed` trong `integration-service`, và 403 khi thiếu `capability:tool_integration`.
3. Đổi tên platform giả định trong `apps/manager-web/src/routes/DashboardPage.test.tsx` khỏi
   `"toolx_social"` để không đụng rule "no hardcoded tool name" của e2e.
