# integration-service

Cổng duy nhất cho Tool/hệ thống ngoài: client, credential, resource, snapshot, ingest, job, audit.

| | |
|---|---|
| Cổng | 8431 |
| Database (riêng) | `integration_db` (user `integration_svc`) |
| Package | `integration_service` |
| Trạng thái | Phase 2 phần lõi **đã hiện thực** (xem bảng module). `jobs`, `legacy_acl` (Phase 2b) còn `planned` |
| Hợp đồng | `docs/api/integration-api-v1.md`, `docs/api/ingest-data-contract-v1.md`, `docs/api/internal-feed-contract-v1.md`, `docs/api/service-auth-contract-v1.md` |
| OpenAPI đóng băng | `docs/api/openapi/integration-service.v1.json` (test so khớp với `/openapi.json`) |

## Module

| Module | Phase | Trạng thái | Trách nhiệm |
|---|---|---|---|
| `clients` | 2.1 | active | Integration Client và liên kết với account |
| `credentials` | 2.1 | active | cấp, xoay, thu hồi API key (lưu hash), scope; xác thực mặt phẳng máy |
| `installations` | 2.1 | active | installation/thiết bị và handshake |
| `platforms` | 2.2 | active | Platform Registry và Metric Registry (dữ liệu, không phải code) |
| `projects` | 2.2 | active | project/source namespace, tự upsert |
| `resources` | 2.2 | active | resource (page/channel/profile), liên kết project; batch ngoài snapshot, admin bật/tắt |
| `idempotency` | 2.2 | active | kho Idempotency-Key và phát lại response |
| `snapshots` | 2.3 | active | state machine snapshot, staging, commit atomic, current pointer, TTL |
| `ingest` | 2.3 | active | schema/validate/merge: resources, content, metrics, events (LWW theo `observed_at`) |
| `feed` | 2.3 | active | outbox + change feed (pull) + canonical export |
| `provisioning` | 2.6 | active | consumer feed Identity: tạo client từ `account.registered`, vòng đời theo account |
| `audit` | 2.2 | active | audit client/installation/request |
| `legacy_acl` | 2.4 | planned | (Phase 2b) |
| `jobs` | 2.5 | planned | (Phase 2b) |

## Ba mặt phẳng xác thực (độc lập)

| Mặt phẳng | Credential | Endpoint |
|---|---|---|
| USER | access token RS256 (Identity ký, verify offline bằng JWKS) | `/v1/integrations/clients*`, `/audit`, `/admin/*` |
| MÁY | API key `mpk_<key_id>.<secret>` + scope + platform | `/v1/integrations/handshake`, `/projects/*`, `/resources/batch`, `/snapshots*` |
| NỘI BỘ | `X-Service-Key` (mỗi bên đọc một khoá) | `/internal/v1/feed`, `/internal/v1/canonical/*` (không qua gateway, không nằm trong OpenAPI) |

Token user không dùng được ở endpoint máy và ngược lại; service key không gọi được API công khai.

## Thiết kế đáng chú ý

- **Ownership suy từ credential.** Mọi object trong payload là `additionalProperties:false`; trường `owner_*` ở bất kỳ cấp nào => `422 RECORD_VALIDATION_FAILED` cho cả request.
- **Commit atomic.** `building → committing → committed | failed | abandoned`. Commit là MỘT transaction (kiểm `expected_counts`/rejections → merge staging→canonical → suy luận `removed` → gán `revision` → outbox → lật `current`). Lỗi hợp đồng (`SNAPSHOT_INCOMPLETE`) mở lại snapshot về `building`; lỗi bất ngờ => `failed`; DB chập chờn => về `building` (503). Tool chết trước commit: canonical không đổi; worker/lazy đánh `abandoned` sau `INTEGRATION_SNAPSHOT_TTL_S` không hoạt động.
- **Thứ tự commit = thứ tự seq/revision.** `pg_advisory_xact_lock` lấy TRƯỚC khi cấp `outbox.seq` và `canonical_revision_seq`, giữ tới hết transaction (`feed/repository.py`). Consumer kéo theo con trỏ không bao giờ mất sự kiện/bản ghi; có test chứng minh và test đột biến (gỡ khoá => mất).
- **Idempotency.** Giữ chỗ khoá nằm trong transaction nghiệp vụ; hai request trùng khoá chạy đồng thời được unique index tuần tự hoá. Chỉ phản hồi thành công được lưu, phát lại đúng từng byte (`Idempotent-Replayed: true`). `Idempotency-Key` là tuỳ chọn ở API (khuyến nghị cho mọi POST/PUT ghi).
- **LWW + null≠0.** `observed_at` mới hơn thắng (hoà: bản sau), cũ hơn => `stale_ignored`; metric là số tuyệt đối ghi đè; JSON `null` và `0` được lưu/xuất nguyên vẹn và gửi lại `0→null` là thay đổi thật. Sự kiện (`events`) bất biến: bản đầu tiên thắng.
- **Removed.** Snapshot `full` + `scope` => bản ghi thuộc phạm vi (cùng owner, **cùng client**, cùng platform) nhưng vắng mặt bị `removed=true` (lịch sử giữ lại). Snapshot cũ hơn một `full` đã commit cùng loại phạm vi chỉ bỏ phần suy luận (`warnings: SNAPSHOT_SUPERSEDED`) và không lật `current` lùi.
- **Canonical export** `GET /internal/v1/canonical/{kind}?after_revision=` và `/summary` (checksum = sha256 của các dòng `<id>:<revision>:<removed>` sắp theo id). Feed: `snapshot.committed`, `resource.changed`, `integration.client.provisioned|provision_failed`, `credential.revoked`.
- **Provisioning.** Consumer là asyncio task trong lifespan, tắt khi thiếu `INTEGRATION_IDENTITY_FEED_URL/KEY`. Con trỏ bền `feed_cursor`, `processed_events` idempotent, retry backoff mũ có giới hạn, `dead_letters` (+ `python -m integration_service.provisioning replay`), không chặn hàng đợi, không crash khi Identity chết.
- **Rate limit** trong bộ nhớ theo `client_id` (mặc định 600/phút) và theo IP cho lần xác thực sai (30/phút). Mỗi worker có bộ đếm riêng.
- **Body**: ≤ 2 MiB (kể cả sau giải nén gzip) => `413 PAYLOAD_TOO_LARGE`; ≤ 500 bản ghi/batch => `413 BATCH_TOO_LARGE`.

## Chạy

```bash
cd services/integration-service
python -m integration_service.migrate                    # job migration riêng (advisory lock, checksum)
python -m uvicorn integration_service.main:app --host 127.0.0.1 --port 8431
python -m pytest -q -p no:cacheprovider                  # cần hạ tầng DEV (Postgres thật) — xem infra/docker/README.md
python -m integration_service.openapi_dump --write       # sinh lại bản OpenAPI đóng băng (chỉ khi thay đổi CÓ CHỦ ĐÍCH)
```

Biến môi trường: xem `.env.example`. Thiếu `INTEGRATION_DATABASE_URL`, `INTEGRATION_ISSUER` hoặc `INTEGRATION_IDENTITY_JWKS_URL` => service từ chối khởi động (fail-fast). DB chết => service vẫn sống, `/health/ready` = 503 và API trả `503 UNAVAILABLE` nhanh.

## Giới hạn đã biết

- Rate limit không chia sẻ giữa nhiều worker/instance.
- Staging giữ toàn bộ payload của snapshot trong Postgres cho tới khi commit/abandon (đã giới hạn 2 MiB × 500 bản ghi mỗi request, 50 snapshot `building` mỗi client).
- Checksum `summary` tính bằng SQL trên toàn bảng (đủ cho quy mô v1; cần cache/materialize khi bảng rất lớn).
- `jobs`, `legacy_acl` và các endpoint legacy `/v1/resources/*`, `/v1/ops/*`, `/v1/viewer/links*` thuộc Phase 2b.

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
