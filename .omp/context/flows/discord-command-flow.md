# Flow: Cơ chế gọi lệnh Discord & xác nhận kết quả
<!-- Nguồn: reward-service/discord/{gateway_session,http,slash_commands,channel_messages}.py, engine/{executor_slash,confirmation}.py -->

Lưu ý quan trọng: reward-service **KHÔNG phải bot lắng nghe interaction từ Discord** — nó dùng **user
token** (đã mã hoá Fernet trong DB) để **tự gọi slash command của 1 bot leveling khác** (vd Axolink
Management) rồi đọc phản hồi của bot đó qua Gateway/REST. Đây là mô phỏng hành vi người dùng, không
phải bot chính chủ. File này mô tả CƠ CHẾ gọi lệnh (khác `reward-job-lifecycle.md` mô tả state machine
job/item bao quanh nó).

## Entry Points (nội bộ, không phải HTTP route)

| Hàm | File | Vai trò |
|---|---|---|
| `fetch_guild_command` | `discord/slash_commands.py:29-53` | Lấy metadata slash command của guild (`GET /guilds/{guild_id}/application-command-index`), trích `member_option`(type 6)/`amount_option`(type 4) |
| `invoke_slash_command` | `discord/slash_commands.py:63-91` | `POST {api_base}/interactions`, `type:2`, `data.options=[member,amount]`, `session_id`, `nonce` |
| `execute` | `engine/executor_slash.py:84-134` | Điều phối: resolve user → lấy anchor message → gọi slash command |
| `await_confirmation` | `engine/confirmation.py:89-164` | Poll message kênh sau khi gọi lệnh, khớp pattern xác nhận kết quả |

## Luồng chính

1. **Kết nối Gateway** — `discord/gateway_session.py:GatewaySession`
   - Mở WebSocket tới `REWARD_GATEWAY_URL` (`wss://gateway.discord.gg/?v=9&encoding=json`), `IDENTIFY`
     bằng user token với `capabilities=30717`, **không RESUME** (luôn identify mới).
   - `get_session_id()` (dòng 122-123) — cần cho `invoke_slash_command`.
   - `request_guild_members(guild_id, query, limit)` (dòng 125-142) — gửi `op 8
     REQUEST_GUILD_MEMBERS`, chờ `GUILD_MEMBERS_CHUNK` khớp FIFO.

2. **Resolve user nhận thưởng** — `executor_slash.py:execute`, thứ tự ưu tiên:
   - `explicit_id` (regex khớp Discord snowflake trong input) → dùng thẳng.
   - `reward_user_map` cache (`user_map_repo.lookup`, bảng `reward_user_map`) — đã resolve trước đó.
   - Gateway `request_guild_members` — tìm theo username, lưu cache nếu thấy
     (`user_map_repo.save`, `source='member_search'`).
   - Không resolve được, hoặc `point<=0` → `ExecuteOutcome(outcome="fail")`, dừng ở đây (không gọi
     Discord).

3. **Gọi lệnh** — resolve OK →
   - Lấy `anchor_message_id` = message mới nhất trong kênh TRƯỚC khi gọi lệnh
     (`discord/channel_messages.py:fetch_channel_messages`) — dùng làm mốc để lọc phản hồi sau này.
   - `session.get_session_id()`.
   - `discord/slash_commands.py:invoke_slash_command` → `outcome ∈ {invoked, http_error,
     network_error}` tuỳ response.

4. **Xác nhận kết quả** — `engine/confirmation.py:await_confirmation` (chỉ chạy khi `outcome=invoked`)
   - Poll `GET /channels/{channel_id}/messages?after=<anchor_message_id>` mỗi
     `confirm_poll_interval_s` (default 2s) tới `confirm_timeout_s` (default 15s).
   - Lọc message có `author.bot=True` (và khớp `leveling_bot_id` nếu account có cấu hình).
   - Xác định `link_mode` theo thứ tự ưu tiên: `reply_ref` (message reply đúng lệnh) → `bot_id` (đúng
     bot cấu hình) → `heuristic` (đoán theo thời gian/nội dung).
   - So khớp `success_pattern`/`failure_pattern` trên `content` + toàn bộ field `embeds`
     (`_text_of`, dòng 51-69).
   - Trả `ConfirmationResult(status, confirmation_level, ...)`:
     - `status=success` → `confirmation_level` = `bot_reply` (khớp success_pattern) hoặc
       `message_posted`/`bot_reply_unmatched` tuỳ mức chắc chắn.
     - Timeout không có message bot phù hợp / message có nhưng không khớp pattern nào /
       phát hiện xung đột (2 message ứng viên) → `status=unknown`.

5. **Kết quả đưa ngược về `job_runner.py`** — xem `reward-job-lifecycle.md` bước 3d để biết cách
   `success`/`failed`/`unknown` chuyển thành trạng thái item + ghi `reward_distributions`.

## Lớp REST dùng chung — `discord/http.py:DiscordHttpClient.request` (dòng 39-92)

- Retry 429 (rate limit Discord) theo thuật toán gốc: `retry_after` đọc từ body JSON → nếu không có,
  đọc header → mặc định 5s. Thời gian chờ thực tế =
  `ceil(retry_after * 1000) + 300 + retry_count * 1000` (ms), tối đa 10 lần retry.
- Timeout theo pha: `connect=5s, write=10s, read=REWARD_HTTP_TIMEOUT_S(default 15s), pool=5s`.
- `retry_on_network_error` luôn `False` trong luồng reward (cả `executor_slash` lẫn `confirmation`) —
  **request gọi lệnh KHÔNG được tự động retry ở tầng HTTP** vì không idempotent; retry (nếu có) xảy ra
  ở tầng nghiệp vụ (`error_classifier` → item `retrying`), không phải tầng transport.

## Slash command index

`discord/slash_commands.py:fetch_guild_command` (dòng 29-53): `GET
{api_base}/guilds/{guild_id}/application-command-index`, tìm command theo `command_name` (default
`give-xp`), trích ra `member_option`(type 6 = USER) và `amount_option`(type 4 = INTEGER) — 2 tham số bắt
buộc để build `data.options` khi `invoke_slash_command`.

## Xử lý lỗi

| Điều kiện | Kết quả |
|---|---|
| Không resolve được user / `point<=0` | `outcome=fail` → item `failed`, không gọi Discord |
| HTTP 429 khi gọi Discord | Tự retry trong `DiscordHttpClient` (tối đa 10 lần), KHÔNG phải lỗi nghiệp vụ |
| HTTP 401/403/404 | `error_classifier` → `pause_job` → `auto_pause` toàn bộ job ngay |
| Timeout xác nhận (15s mặc định) | item `unknown`, `consecutive_unknown+=1` |
| Message bot không khớp pattern nào | item `unknown` (không phải `failed`) |

## [chưa xác định]

- `discord/channel_messages.py:post_channel_message` (dòng 44-64) tồn tại trong module nhưng KHÔNG thấy
  call site trong `engine/`/`api/` đã đọc — có thể dự phòng/legacy, chưa xác nhận mục đích sử dụng thực
  tế.

## Dependencies

- Module: `reward-service/discord/*.py`, `reward-service/engine/{executor_slash,confirmation}.py`.
- External: Discord Gateway (WS), Discord REST API v9 (`https://discord.com/api/v9` mặc định).
- Database tables: `reward_user_map` (cache resolve), `reward_attempts` (ghi kết quả từng bước).
