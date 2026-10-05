<!-- Nguồn: admin-server/db.py (DDL), reward-service/store/migrations/001_reward_init.sql,
     D:/workspace/upload-facebook/server/db.py (schema `documents`/`revs`/`user_meta` — ĐỌC TỪ REPO TOOL,
     KHÔNG suy đoán), D:/workspace/upload-facebook/app/sync/registry.py (whitelist store),
     D:/workspace/upload-facebook/.omp/context/{ARCHITECTURE.md,INTEGRATIONS.md,SECURITY.md}.
     Sinh bởi vibe-init 2026-09-28, đối chiếu 2 repo. -->

# data-models.md

## 1. Bảng do admin-server SỞ HỮU (đọc + ghi) — `admin-server/db.py` DDL

Tồn tại song song 2 phương ngữ SQL (`_SQLITE_DDL` dev / `_PG_DDL` production), field giống nhau, chỉ
khác kiểu `TEXT/INTEGER` (SQLite) vs `text/integer/boolean` (Postgres).

### `invite_keys`
| Field | Type | Ghi chú |
|---|---|---|
| `key` | TEXT/text PK | dạng `XXXX-XXXX`, sinh bởi `_gen_key()` |
| `role` | TEXT default `'viewer'` | pattern `viewer\|admin\|discord` — role sẽ gán cho account đăng ký bằng key này |
| `label` | TEXT default `''` | ghi chú tự do |
| `max_uses` | INTEGER default 1 | |
| `used_count` | INTEGER default 0 | tăng ATOMIC: `UPDATE...WHERE status='active' AND used_count<max_uses` |
| `created_at` | TEXT (ISO8601 UTC) | |
| `expires_at` | TEXT default `''` | |
| `status` | TEXT default `'active'` | `active\|suspended` |

### `accounts` — tài khoản VIEWER Dashboard (KHÔNG liên quan `accounts.json` của Tool — xem mục 4)
| Field | Type | Ghi chú |
|---|---|---|
| `id` | TEXT/text PK | uuid4 hex |
| `username` | TEXT UNIQUE | pattern `^[a-zA-Z0-9_]{3,30}$` |
| `email` | UNIQUE, lower-cased | Pydantic `EmailStr` |
| `password_hash` | TEXT | bcrypt cost 12 |
| `role` | TEXT default `'viewer'` | `viewer\|admin\|discord\|dash_admin` — admin_api chỉ cho set `viewer\|admin\|dash_admin` qua API (`dash_admin`="Admin Dashboard/ADD", cộng thêm, không thay `admin`); `discord` chỉ gán được qua invite-key lúc register hoặc `reward-service POST /v1/reward/admin/accounts/{id}/grant-discord` (UPDATE trực tiếp, không CHECK ở DB) |
| `invite_key` | TEXT | key đã dùng để đăng ký |
| `email_verified` | INTEGER/boolean default 0/false | |
| `verify_token` | TEXT default `''` | uuid4 hex, TTL 24h |
| `verify_token_at` | TEXT default `''` | |
| `reset_token` | TEXT default `''` | dùng chung cho reset-password (uuid4, TTL 1h) VÀ mã đổi mật khẩu (6 số, TTL 600s hardcode) |
| `reset_token_at` | TEXT default `''` | |
| `created_at` | TEXT | |
| `last_login` | TEXT default `''` | |
| `status` | TEXT default `'pending'` | `pending\|active\|suspended` |

### `refresh_tokens`
| Field | Type | Ghi chú |
|---|---|---|
| `token` | TEXT/text PK | |
| `account_id` | TEXT/text | FK logic → `accounts.id` |
| `created_at` | TEXT | |
| `expires_at` | TEXT | now + 30 ngày |
| `revoked` | INTEGER/boolean default 0/false | |

### `account_links` — liên kết account dashboard ⇄ `user_id` Tool (thêm 2026-09-30)
| Field | Type | Ghi chú |
|---|---|---|
| `account_id` | TEXT/text | FK logic → `accounts.id`, PK phần 1 |
| `tool_user_id` | TEXT/text | = `documents.user_id` (HMAC license key phía Tool), PK phần 2 |
| `label` | TEXT default `''` | chưa có UI đặt tên, dự trù |
| `status` | TEXT default `'active'` | `active\|revoked` |
| `linked_at` | TEXT | ISO8601 UTC |

### `link_codes` — mã pairing dùng 1 lần, TTL 15 phút (thêm 2026-09-30)
| Field | Type | Ghi chú |
|---|---|---|
| `code` | TEXT/text PK | 8 ký tự hex in hoa (`secrets.token_hex(4).upper()`) |
| `account_id` | TEXT/text | account đã tạo mã |
| `created_at`/`expires_at` | TEXT | ISO8601 UTC |
| `used_at` | TEXT default `''` | |
| `status` | TEXT default `'pending'` | `pending\|used\|expired` |

### `channel_groups`/`channel_group_members` — group/tag kênh do viewer tự quản lý (thêm 2026-09-30)
KHÔNG liên quan `repost_groups` của Tool (mục 3 bên dưới) — bảng này thuần metadata tổ chức phía
dashboard, sở hữu 100% bởi admin-server.
| Bảng | Field chính | Ghi chú |
|---|---|---|
| `channel_groups` | `id`(PK uuid4 hex), `account_id`, `name`, `created_at`, `updated_at` | chủ sở hữu = `account_id`, chỉ chủ sửa được |
| `channel_group_members` | `group_id`+`channel_ref`(PK kép), `platform_tag`, `country_override`, `created_at` | `channel_ref` = `target` (FB) hoặc `id` kênh YouTube đã xuất hiện trong dữ liệu Tool của account đó; `platform_tag` là nhãn tự do (vd "tiktok") — đáp ứng yêu cầu đa nền tảng KHÔNG cần sửa `stats_core.py` |

## 2. Bảng ĐỌC-ONLY từ Tool (admin-server KHÔNG có quyền ghi — comment `db.py`: "admin-server chỉ ĐỌC nên không có quyền ALTER")

Schema THẬT lấy trực tiếp từ `D:/workspace/upload-facebook/server/db.py` (sync server VPS — process
tách biệt hoàn toàn với admin-server, không import gì từ `app/` của Tool):

### `documents` — key-value đa-tenant generic (mỗi "store" = 1 loại dữ liệu JSON được đồng bộ)
```sql
CREATE TABLE documents (
  user_id    text    NOT NULL,
  store      text    NOT NULL,
  key        text    NOT NULL,
  payload    text,                 -- chuỗi JSON (KHÔNG phải jsonb); NULL = tombstone (đã xoá)
  rev        bigint  NOT NULL,     -- con trỏ thứ tự, server cấp, LUÔN TĂNG
  clock      text    NOT NULL DEFAULT '',  -- đầu vào phép gộp local, KHÔNG được gộp chung với rev
  deleted    boolean NOT NULL DEFAULT false,
  device_id  text    NOT NULL DEFAULT '',
  PRIMARY KEY (user_id, store, key)
);
CREATE INDEX documents_rev ON documents (user_id, rev);
```
Ghi bằng `INSERT ... ON CONFLICT (user_id, store, key) DO UPDATE`. `/v1/push` idempotent (nội dung
không đổi thì không ghi). Trần: `MAX_OPS=500`/lượt đẩy, `MAX_PULL=1000`.

admin-server đọc production bằng SQL trực tiếp dạng `SELECT payload FROM documents WHERE store='<tên>'
AND NOT deleted` (`admin-server/db.py:925-1006`), với `<tên>` là 1 trong các **store admin-server thực
sự dùng để tính thống kê**: `fb_posts`, `yt_insights_daily`, `repost_events`, `repost_pairings`,
`repost_groups`, `fb_page_meta` (+ fallback `fb_pages`), `yt_channels`. Danh sách store khả dụng đầy đủ
(whitelist toàn bộ, không phải tất cả admin-server dùng) khai ở
`upload-facebook/app/sync/registry.py:80-152`, gồm cả các store admin-server KHÔNG đọc: `repost_videos`
(KHÔNG sync — ở lại máy, chống đăng trùng), `translate_sources/variants`, `projects`, `accounts.json`
của Tool (CẤM sync tuyệt đối), `repost_cookies`, token OAuth (`yt_channels`/`fb_pages`/`fb_apps`/
`yt_apps` — phần token là `secret_fields`, mã hoá bằng `crypto.seal` tại `server/db.py` trước khi lưu
`payload`, giải mã khi đọc).

### `revs`
```sql
CREATE TABLE revs (user_id text PRIMARY KEY, next_rev bigint NOT NULL);
```
admin-server chỉ dùng `SUM(next_rev)` (mọi user) để tính con dấu phiên bản `doc_version` cho cache —
đổi tổng = có ghi mới ở đâu đó → cache stats bị coi là cũ (`admin-server/cache.py`). Schema đầy đủ chỉ
có 2 cột này (đã xác nhận DDL, không phải suy đoán).

### `user_meta`
```sql
CREATE TABLE user_meta (
  user_id text PRIMARY KEY, label text, features text, first_seen text,
  last_seen text, device_id text, license_key text  -- mã hoá bằng crypto.seal
);
```
[chưa xác định] admin-server có đọc bảng này hay không — không thấy trong phần `db.py` đã scout; đây là
bảng dùng cho UI admin RIÊNG của Tool (`app/admin/` ↔ `server/admin.py`), không phải admin_manager.

## 3. Cấu trúc file JSON — chế độ `dev` (`LocalFileDb` đọc thẳng `DATA_DIR` = `APP_DATA_DIR` của Tool)

Đây CHÍNH LÀ nội dung mà mỗi `payload` trong `documents` (ứng với từng `store`) chứa khi giải nén JSON —
admin-server đọc 2 nguồn (JSON local hoặc Postgres) nhưng ra **cùng shape** để đưa vào `stats_core.py`.

| File JSON (trong `APP_DATA_DIR`) | Tương ứng `store` (Postgres) | Shape | Field/logic lọc ở admin-server |
|---|---|---|---|---|
| `fb_posts.json` | `fb_posts` | `posts_by_target`: `{target: [post,...]}` | Gắn thêm `target` vào mỗi post; lọc `country` nếu post có field đó |
| `yt_insights_daily.json` | `yt_insights_daily` | `dict_of_records`: `{"YYYY-MM-DD\|channel_id": record}` | Lọc theo `record.country` |
| `repost_events.json` | `repost_events` | `list_by_id`: `[{id,kind,date,target,...}]` | Chỉ giữ record có field `kind` |
| `repost_pairings.json` | `repost_pairings` | `dict_of_records`: `{id: pairing_record}` | Chỉ giữ record có field `target` |
| `repost_groups.json` | `repost_groups` | `list_by_id`: `[{id,name,country,enabled,...}]` | Chỉ giữ record có field `id`; `stats_core.cho_chay(group)` xác định nhóm có cho reup chạy |
| `fb_page_meta.json` (fallback `fb_pages.json`) | `fb_page_meta` | `dict_of_records`: `{target: {target,name,country}}` | Dùng cho `target_country_map`/`target_names`; method load cụ thể [chưa xác định tên hàm, chỉ xác nhận qua điểm gọi] |
| `yt_channels.json` | `yt_channels` | container `{"channels":[{id,title,country}]}` | |

**Ngữ nghĩa quan trọng** (từ docstring `stats_core.py`, áp dụng cả 2 nguồn dữ liệu): trong chuỗi view
theo ngày, `None` = "chưa có dữ liệu", `0` = "có dữ liệu nhưng không ai xem" — **KHÔNG được ép None→0**
khi sửa/thêm logic.

`accounts.json` của Tool (cookie phiên Facebook gắn với profile browser) **CẤM đồng bộ tuyệt đối**
(`app/sync/registry.py` `FORBIDDEN`) — không bao giờ xuất hiện trong `documents`, và admin-server cũng
không đọc file này để tính thống kê. **Không được nhầm với bảng SQL `accounts` ở mục 1** (tài khoản
viewer Dashboard) — 2 khái niệm hoàn toàn khác nhau, chỉ trùng tên.

## 4. Data model reward-service (`reward-service/store/migrations/001_reward_init.sql`) — sở hữu riêng

12 bảng, tất cả prefix `reward_` (trừ đọc `accounts` ở mục 1, không có quyền ALTER/DROP bảng
admin-server):

| Bảng | Khoá | Field chính | Ghi chú |
|---|---|---|---|
| `reward_schema_migrations` | `version text PK` | `applied_at timestamptz` | |
| `reward_discord_credentials` | `account_id text PK` (FK mềm→`accounts.id`) | **Cấu hình** (không còn token): `guild_id`, `channel_id`, `command_name`(default `give-xp`), `confirm_mode`(`off\|reply`), `success_pattern`, `failure_pattern`, `leveling_bot_id`, `delay_ms`(≥0,default 3000), `jitter_ms`(≥0,default 500), `status`(`unverified\|valid\|invalid\|revoked`; `valid` ⇔ có ≥1 token bật + hợp lệ), `last_error`, `verified_at`, `created_at/updated_at` | 1 row/account |
| `reward_discord_tokens` | `id bigserial PK` | `account_id`(FK mềm), `label`, `token_ciphertext`(Fernet), `discord_user_id/username`, `status`(`unverified\|valid\|invalid`), `enabled`, `last_error`, `verified_at`, `last_used_at`, `created_at/updated_at` | N token/account, xoay vòng khi chạy job (`engine/token_pool.py`). UNIQUE `(account_id, discord_user_id)` (partial). Migration `002_reward_tokens.sql` chuyển token cũ từ credentials sang đây. `reward_attempts.token_id` ghi token đã gửi |
| `reward_jobs` | `id bigserial PK` | `account_id`(FK mềm), `name`, `status`(9 giá trị — mục "Job status"), snapshot cấu hình lúc tạo (`guild_id..jitter_ms`), `max_item_retries`, `unknown_pause_threshold`, `total_items`, `source_name/hash`, `created_at/validated_at/started_at/finished_at` | Index `(account_id,created_at DESC)`, `(status)` |
| `reward_items` | `id bigserial PK` | `job_id`(FK CASCADE), `row_index`, `raw_username`, `normalized_username`, `resolved_user_id`, `point`, `status`(7 giá trị), `resolve_level`(`explicit_id\|local\|member_search`), `confirmation_level`(`none\|message_posted\|bot_reply_unmatched\|bot_reply`), `failure_code/message`, `attempt_count`, `first_sent_at/last_attempt_at/finalized_at`, `resolved_by`, `idempotency_key` | UNIQUE `idempotency_key`, UNIQUE `(job_id,row_index)` |
| `reward_attempts` | `id bigserial PK` | `item_id`(FK CASCADE), `job_id`, `attempt_no`, `nonce`, `phase`(`prepared\|sent\|confirmed\|rejected\|aborted`), `command_text`, `http_status`, `message_id`, `posted_at`, `reply_message_id/author_id/excerpt`, `link_mode`, `error_code/message`, `started_at/finished_at` | UNIQUE `nonce` |
| `reward_distributions` | `item_id bigint PK` (FK RESTRICT) | `attempt_id`(FK), `job_id`, `account_id`, `discord_user_id`, `point`, `evidence jsonb`, `distributed_at` | Sổ cái — PK=item_id chặn phân phối trùng tuyệt đối |
| `reward_validation_issues` | `id bigserial PK` | `job_id`(FK CASCADE), `row_index`, `severity`(`error\|warning`), `code`, `message` | |
| `reward_job_events` | `id bigserial PK` | `job_id`(FK CASCADE), `at`, `type`(created\|validated\|started\|auto_pause\|paused\|resumed\|stopped\|completed\|recovery\|warn_no_pattern\|operator_resolve), `actor`, `payload jsonb` | Index `(job_id,id)` |
| `reward_runner_lock` | `job_id bigint PK` | `account_id`, `channel_id`(UNIQUE), `pid`, `host`, `heartbeat_at` | 1 kênh Discord chỉ 1 job chạy cùng lúc |
| `reward_user_map` | `(account_id,normalized_username)` PK | `discord_user_id`, `source`(default `member_search`), `updated_at` | cache resolve username→user_id |
| `reward_role_audit` | `id bigserial PK` | `account_id`, `actor_account_id`, `from_role`, `to_role`, `reason`, `at` | Index `(account_id,at DESC)` |

### Enum state chi tiết
- **Item status**: `pending→processing→{success,failed,unknown,retrying}`; `unknown→{success,pending}`
  CHỈ hợp lệ khi `actor='operator'` (không bao giờ tự động); `skipped` khai báo CHECK nhưng chưa thấy
  code set (dự trù).
- **Job status** (CHECK, 9 giá trị): `draft, validating, validated, invalid, running, paused, stopping,
  stopped, completed` — `validating`/`stopping` có trong CHECK nhưng KHÔNG thấy code set (dự trù).
- **Attempt phase**: `prepared`(write-ahead lúc claim) `→ sent → {confirmed,rejected,aborted}`.
- **Credential status**: `unverified → {valid, invalid, revoked}`.

## 5. Vòng đời đồng bộ 2 chiều — điểm chạm cần biết khi sửa cả 2 repo

- Ba bất biến đồng bộ (`upload-facebook/app/sync/applier.py`, không được phá): (1) ảnh chụp ghi khớp
  thứ vừa áp trong cùng thao tác; (2) sửa đổi local chưa đẩy không bao giờ bị nuốt; (3) con trỏ không
  vượt bản ghi đã bỏ (server chỉ trả `rev > con trỏ`).
- `fresh_cache` (`upload-facebook/app/sync/deps.py`) là tầng "kéo từ VPS" phía Tool khi đọc — đệm
  ≤120s, single-flight, không ném lỗi khi offline. **Không liên quan** `admin-server/cache.py`
  (`CachedDb`) — đây là 2 lớp cache độc lập ở 2 repo khác nhau, cùng nguyên lý nhưng KHÔNG dùng chung
  code/Redis instance.
- Đổi shape 1 store JSON (vd thêm field mới vào `fb_posts.json`) ở Tool → PHẢI kiểm tra
  `admin-server/db.py` (`_load_fb` và tương đương) có đọc field đó không, và `stats_core.py` 2 bên có
  còn khớp byte-identical không.
