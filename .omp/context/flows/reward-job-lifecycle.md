# Flow: Vòng đời Reward Job (tạo → chạy → xác nhận → phân phối)
<!-- Nguồn: admin-web/src/reward/pages/{JobNewPage,JobDetailPage}.tsx → reward-service/api/jobs.py → reward-service/engine/{worker_manager,job_runner,executor_slash,confirmation,recovery}.py → store/repositories/*.py -->

## Entry Points

| Method | Path | File | Middleware |
|---|---|---|---|
| POST | `/v1/reward/jobs` | `api/jobs.py:167-190 create_job` | Active + role∈{discord,admin} (T2) |
| POST | `/v1/reward/jobs/{id}/run` | `api/jobs.py:332-345 run_job` | Active + Reward |
| POST | `/v1/reward/jobs/{id}/pause` | `api/jobs.py:351-364 pause_job` | Active + Reward |
| POST | `/v1/reward/jobs/{id}/stop` | `api/jobs.py:366-379 stop_job` | Active + Reward |
| POST | `/v1/reward/jobs/{id}/items/{item_id}/resolve` | `api/jobs.py:382-413 resolve_item` | Active + Reward, operator only |
| GET | `/v1/reward/jobs/{id}/stream` | `api/jobs.py:277-311 stream` | Reward — SSE, poll 0.5s |

## Luồng chính

1. **Tạo job (draft)** — `POST /v1/reward/jobs` → `api/jobs.py:create_job`
   - Lấy `reward_discord_credentials` của account (`credentials_repo.get_by_account`).
   - Gộp `overrides` body đè lên credentials mặc định (guild/channel/command/confirm_mode/pattern/
     leveling_bot_id/delay/jitter).
   - `_validate_job_config` (`api/jobs.py:107-120`): guild/channel bắt buộc, `confirm_mode∈{off,reply}`,
     regex hợp lệ.
   - `jobs_repo.create_job(status="draft", ...)` — **CHỤP toàn bộ cấu hình vào `reward_jobs`** (sửa
     credentials sau đó KHÔNG ảnh hưởng job đã tạo).
   - `events_repo.add_event(type="created")`.
   - `_run_validation` (`api/jobs.py:466-491`) — chèn item vào `reward_items` với
     `idempotency_key = sha256(job_id|row_index|normalized_username|point)`
     (`domain/idempotency.py:item_key`), chèn `reward_validation_issues`, set job `validated`/`invalid`.

2. **Chạy job** — `POST /v1/reward/jobs/{id}/run` → `engine/worker_manager.py:WorkerManager.start`
   - Kiểm đồng bộ TRƯỚC khi chiếm lock / đặt `running` (start thất bại không để job kẹt `running`):
     `len(_running)>=max_concurrent_jobs` → 429; job/account không hợp lệ → 403; credential (cấu hình)
     chưa `status='valid'` → 403; không có token `enabled` + `valid` → 403.
   - Job cũ có `success_pattern` rỗng (chụp lúc cấu hình chưa có pattern) → bổ sung từ credential/mặc
     định `domain/defaults.py:DEFAULT_SUCCESS_PATTERN` và ghi lại vào job.
   - `_build_slots`: giải mã MỖI token (`crypto.TokenCrypto.decrypt`) + `fetch_guild_command` bằng chính
     token đó; token không thấy lệnh → `mark_invalid` + loại khỏi bể; không token nào thấy lệnh → 400
     `CommandNotFoundError`.
   - Chiếm khoá `reward_runner_lock` theo `channel_id` (`store/repositories/lock.py:acquire`) — lock
     "tươi" (<30s) của job khác → 409. `jobs_repo.set_status("running", started_at=now())`.
   - Tạo `TokenPool` (mỗi token 1 `GatewaySession`) + `ExecutorConfig`/`ConfirmationConfig`/
     `JobRunnerConfig` → chạy nền `asyncio.create_task(JobRunner.run(job_id))`.

3. **Vòng lặp chính** — `engine/job_runner.py:JobRunner.run`
   - Re-acquire lock, set `running`, khởi `_heartbeat_loop` (renew lock mỗi 5s).
   - `while not (stop_requested or pause_requested)`:
     a. `TokenPool.acquire()` — chọn NGẪU NHIÊN trong token sẵn sàng (không lặp token vừa dùng nếu còn lựa chọn);
        khoảng cách 2 lệnh liên tiếp ngẫu nhiên quanh `(delay_ms+jitter_ms/2)/N`; mỗi token vẫn ≥ `delay_ms` giữa 2 lần dùng.
        Sau khi chờ nhịp: `_check_role_and_credential()` — role/status/credential đổi → `auto_pause`.
     b. `items_repo.claim_next` — **write-ahead 1 transaction**: `UPDATE reward_items SET
        status='processing' WHERE id=(SELECT...FOR UPDATE SKIP LOCKED LIMIT 1)` +
        `INSERT reward_attempts(phase='prepared')`. Hết item `pending/retrying` → thoát vòng lặp.
     c. `engine/executor_slash.py:execute` — resolve user (`explicit_id` → `reward_user_map` cache →
        Gateway `REQUEST_GUILD_MEMBERS`) → lấy `anchor_message_id` → `session.get_session_id()` →
        `discord/slash_commands.py:invoke_slash_command` (`POST /interactions`) →
        `outcome∈{invoked,http_error,network_error}` (hoặc `fail` nếu không resolve được/`point<=0`).
     d. Theo `outcome`:
        - `fail` → item `failed` trực tiếp.
        - `invoked` → `attempts_repo.mark_sent` → `engine/confirmation.py:await_confirmation` (poll
          message kênh, khớp `success_pattern`/`failure_pattern`) →
          - `success` → `attempts.finalize(confirmed)` + `items.finalize(success)` +
            **`dist_repo.insert_once`** (PK=`item_id`, chặn ghi trùng tuyệt đối).
          - `failed` (match failure_pattern) → item `failed`.
          - `unknown` (timeout/conflict/reply rỗng) → item `unknown`, `slot.unknown_streak+=1`.
            `await_confirmation` KHÔNG chốt tin bot còn RỖNG (interaction defer) và giữ con trỏ `after`
            ngay trước nó để quét lại tới khi bot điền nội dung.
        - `http_error`/`network_error` → `domain/error_classifier.py:classify` →
          `retry`(còn ngân sách) → item `retrying` (KHÔNG BAO GIỜ về `pending`; 429 ⇒ `cool` token 60s);
          hết ngân sách → `failed`/`unknown`. Lỗi 401/403/404 (Discord từ chối, lệnh chưa chạy) = lỗi
          của TOKEN: `token_disabled` + token `invalid` trong DB; còn token khác → `items_repo.requeue`
          (không tốn ngân sách retry) để token khác gửi; hết token → item `failed` + `auto_pause`.
     e. Chuỗi `unknown` của MỘT token ≥ `unknown_pause_threshold` (default 5) → `token_disabled`;
        `auto_pause` chỉ khi không còn token nào (1 token ⇒ y hệt hành vi cũ).
     f. `TokenPool.release(slot)` — token chỉ dùng lại sau delay+jitter của riêng nó.
   - Kết thúc: `paused`/`stop_requested` → job `paused`/`stopped`; hết việc → job `completed`.
   - `finally`: huỷ heartbeat, `lock_repo.release(job_id)`.

4. **Pause/Stop** — `pause_job`/`stop_job` set cờ trên `JobRunner` đang chạy (nếu cùng tiến trình) hoặc
   set DB trực tiếp; `stop_job` còn tự set `status='stopped'` + release lock + event ở tầng API
   (double-safety).

5. **Operator resolve item `unknown`** — `resolve_item` →
   `domain/item_state.py:assert_transition(status,to,actor='operator')` — chỉ cho `unknown→success|
   pending`; `to=success` bắt buộc `resolved_user_id`, ghi `dist_repo.insert_once` với
   `evidence.level='operator'`.

6. **Theo dõi realtime (frontend)** — `admin-web/src/reward/hooks/useJobStream.ts` → SSE
   `GET /v1/reward/jobs/{id}/stream` (poll DB 0.5s phía server, phát `event: counts|item|job`, ping
   15s) → client đọc `ReadableStream` thủ công (không `EventSource`, cần header Authorization) →
   fallback polling 2s nếu lỗi/đóng stream.

## Recovery sau crash tiến trình

`engine/recovery.py:recover_orphaned_attempts` chạy trong `lifespan` TRƯỚC request đầu tiên:
- Attempt còn `prepared`/`sent` → `aborted` (`error_code=CRASH_DURING_SEND`).
- Item non-terminal tương ứng → `unknown` (`message_posted` nếu đã có `message_id`, ngược lại `none`) —
  **KHÔNG BAO GIỜ đặt lại `pending`** (chống gửi trùng, giữ bất biến write-ahead).
- Job `running` → `paused` + event `recovery`.
- Giải phóng lock quá hạn >30s (`lock_repo.release_stale`).

## Xử lý lỗi

| Điều kiện | Response/Trạng thái |
|---|---|
| Vượt `max_concurrent_jobs` | 429 |
| Account không active / role không đủ / credential chưa valid | 403 |
| Kênh đang bị job khác giữ lock (<30s) | 409 |
| Slash command không tồn tại trên guild | 400, release lock |
| Lỗi Discord 401/403/404 giữa job | `auto_pause` ngay, item còn lại giữ `pending` |
| Crash tiến trình giữa job | Recovery lúc restart — xem trên |

## Side Effects

- Ghi `reward_jobs`, `reward_items`, `reward_attempts`, `reward_distributions`,
  `reward_validation_issues`, `reward_job_events`, `reward_runner_lock`, `reward_user_map`.
- Gọi Discord Gateway (WS) + Discord REST (`/interactions`, `/channels/{c}/messages`).

## Dependencies

- Module: `reward-service/{api/jobs.py,engine/*.py,domain/*.py,discord/*.py,store/repositories/*.py}`.
- Database tables: toàn bộ `reward_*` (xem `data-models.md` mục 4) + đọc `accounts` (T2).

## Chống kẹt `unknown` — đối soát + gửi lại

1. `unknown` chỉ là "chưa thấy xác nhận trong cửa sổ chờ" (reply muộn, tin bot rỗng lúc quét, `POST /interactions`
   lỗi/timeout nhưng Discord vẫn chạy). **Không** tự gửi lại.
2. `engine/reconcile.py:reconcile_job` quét lịch sử kênh và ghép reply theo người nhận + số điểm + success_pattern:
   khớp ⇒ `success` (+`reward_distributions`), khớp failure_pattern ⇒ `failed`, quét hết kênh quá grace mà không có ⇒
   `reconcile_result='no_reply'`.
3. Tự động: `JobRunner._resettle` (trước khi loại token / dừng job vì chuỗi unknown) và `JobRunner._sweep` (trước khi đóng
   trạng thái completed/paused). Thủ công: `POST /jobs/{id}/reconcile`.
4. Gửi lại: `POST /jobs/{id}/retry-unknown` = đối soát → `items_repo.requeue_unconfirmed` (chỉ item `no_reply`) → job
   `completed` chuyển `paused` → `Tiếp tục`. Item còn `unknown` mà chưa `no_reply` (chưa đủ grace / không quét hết) KHÔNG bị gửi lại.
