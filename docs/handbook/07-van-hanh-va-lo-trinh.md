# 7. Vận hành và lộ trình

## 7.1 Vận hành

| Việc | Ở đâu |
|---|---|
| Runbook chuyển production (từng bước + quay lui, **tất cả `PENDING_PRODUCTION_APPROVAL`**) | `docs/operations/production-cutover-runbook.md` |
| Chuyển Identity (shadow sync → parity → single-writer) | `docs/operations/identity-migration-runbook.md` |
| Kế hoạch chuyển DB Reward (`ufsync` → `reward_db`), vendoring SDK | `reward-service/DEPLOY.md` |
| Hạ tầng DEV | `infra/docker/README.md` |
| Báo cáo triển khai + số liệu test | `docs/operations/implementation-final-report.md` |

Lệnh vận hành đã có (đều là job riêng, không chạy tự động lúc khởi động):

| Lệnh | Tác dụng |
|---|---|
| `python -m <identity_service\|integration_service\|analytics_service>.migrate` | áp migration forward-only |
| `python -m identity_service.keys rotate` / `list` | xoay / liệt kê khóa ký |
| `python -m identity_service.oidc_clients seed-dev` | seed OIDC client `manager-web` (DEV) |
| `python -m identity_service.feed_consumer replay` | replay dead-letter (consumer của Identity) |
| `python -m integration_service.provisioning replay` | replay dead-letter provisioning |
| `python -m identity_link.consumer replay` (trong `reward-service/`) | replay dead-letter của Reward |
| `POST /v1/analytics/admin/{rebuild,reconcile,sync}`, `GET/POST …/admin/dead-letters[/replay]` | dựng lại / đối soát / đồng bộ / xử lý dead-letter của Analytics (cần admin) |
| `python infra/gateway/render_nginx.py [--check\|--nginx-test]` | sinh/kiểm `nginx.conf` |

Điều **chưa có** về vận hành: metrics/tracing (chỉ có log JSON + `request_id`), dashboard giám sát, cảnh báo, chính sách retention cho `outbox`/`dead_letters`/`idempotency_keys` (v1: outbox không xóa), backup/restore tự động, image đã build, manifest triển khai (systemd/compose production).

## 7.2 Việc chờ chủ dự án duyệt (🔒) — KHÔNG tự làm

Tạo DB/role/Redis ACL production · cutover Identity (1.3) · nginx + gateway lên VPS · bật Integration/Analytics + backfill · chuyển DB Reward + viết lại `deploy-reward.yml` · deploy web · chuyển `local-ops-hub` · bật lại CI/CD (trigger `push`; sửa `tests/arch/test_ci_guard.py` cùng commit) · `git mv reward-service → services/` · gỡ legacy · nâng major vite/vitest · canonicalizer `documents→snapshot` (cần hạ tầng/sync server) · **mọi `git push`**.

## 7.3 Backlog kỹ thuật cho người tiếp theo (ưu tiên giảm dần)

Mỗi mục: **bối cảnh → việc → nghiệm thu**. Không có mục nào ở đây cần duyệt production; làm hoàn toàn được ở DEV.

| # | Mục | Bối cảnh / việc | Nghiệm thu |
|---|---|---|---|
| 1 | **Prefetch JWKS lúc khởi động** | `TokenVerifier` chỉ tải JWKS khi gặp token đầu tiên ⇒ service khởi động lúc Identity chết trả 401 (E2E phải "làm ấm" Reward trước khi dừng Identity). Thêm warm-up nền có retry trong lifespan của analytics/integration/reward (SDK: `TokenVerifier.prefetch()`) | Test E2E: dừng Identity, khởi động **lạnh** Analytics/Reward → token hợp lệ vẫn qua khi Identity sống lại trong vài giây; không crash khi Identity chết lúc khởi động; bỏ bước làm ấm trong `test_z_fault_isolation.py` |
| 2 | **TTL access token 600 s (D12)** | Mặc định hiện 900 (`config.py` + test parity khóa 900: `test_auth_flow`, `test_oidc_token`, `test_units`). Đổi mặc định RS256 → 600, giữ HS256 compat 900 nếu parity legacy cần; web đã refresh tự động | Test cập nhật có chủ đích + giải thích ở commit; E2E không đổi; web refresh vẫn gộp một lần |
| 3 | **Introspection fail-closed cho thao tác nhạy cảm (D13)** | `/oauth/introspect` đã có nhưng chưa ai gọi. Xác định danh sách thao tác (cấp/xoay/thu hồi credential, cấp permission admin, flush/rebuild analytics) và gọi introspection **fail-closed** (Identity chết ⇒ từ chối) — ghi vào `service-auth-contract-v1.md` | Test: thu hồi `sid` ⇒ thao tác nhạy cảm bị từ chối ngay dù token chưa hết hạn; Identity chết ⇒ thao tác nhạy cảm 503, thao tác thường vẫn chạy |
| 4 | **Đưa mã lỗi riêng vào `manager_contracts.ErrorCode`** | `Extra` của Integration và `INTEGRATION_NOT_ALLOWED/INTEGRATION_LIMIT_EXCEEDED` của Identity đang là chuỗi rời. Thêm (tương thích ngược) và đổi service dùng enum | Test arch/contract: mọi `error.code` phát ra ∈ enum; OpenAPI đóng băng không đổi |
| 5 | **Analytics: PK `read_resources` gồm `resource_type`** | Hai loại resource trùng `external_id` trong cùng platform+owner ghi đè nhau. Migration `0002` đổi PK + sửa `apply`/`channels` | Test: hai resource khác `resource_type` cùng `external_id` cùng tồn tại; parity stats không đổi |
| 6 | **Analytics: `agg_daily`, seal theo owner, single-flight** | Hiện mỗi truy vấn tính từ read model (20k bài/ngày ≈ vài giây, có test) và seal toàn cục. Dùng `agg_daily` khi ổn định; seal theo owner; single-flight chống thundering herd | Test hiệu năng quy mô; commit của owner B không làm mất cache owner A; số liệu giữ nguyên (parity) |
| 7 | **SDK: heartbeat/`lease_token` + SDK TypeScript** | `IntegrationClient` chưa có `heartbeat`; server có `lease_token` tùy chọn. Sinh `packages/sdk-typescript` từ OpenAPI đóng băng | Test SDK với server thật (E2E jobs); TS SDK qua contract test |
| 8 | **Integration: rate limit mặt phẳng user, cache checksum, event `job.*`** | Rate limit user chưa có; checksum tính toàn bảng; hợp đồng feed chưa có `job.completed/failed` (cần thêm vào `internal-feed-contract-v1.md` + registry `emits`) | Test rate limit; benchmark `canonical/summary` quy mô lớn |
| 9 | **Rate limit/breaker dùng chung giữa worker** | Hiện trong bộ nhớ từng tiến trình (gateway, integration). Dùng Redis (user ACL riêng, prefix riêng) hoặc nginx `limit_req` | Test nhiều worker: tổng không vượt ngưỡng |
| 10 | **Kiểm thử web trên trình duyệt thật** | Hiện jsdom + mock `fetch`. Thêm Playwright chạy với `DevStack` + gateway: đăng ký → xác thực → đăng nhập (legacy và OIDC) → Dashboard với dữ liệu Mock Tool X | Suite E2E UI xanh; chụp màn hình bằng chứng |
| 11 | **Web: banner 503 Reward/Integration, tách chunk, nâng vite/vitest** | Dashboard có banner; Reward/Integration chưa. Bundle ≈1 MB. 5 lỗ hổng dev-only cần nâng major (**cần duyệt**) | `npm audit` sạch (sau duyệt); lighthouse/bundle size |
| 12 | **CI GitHub (không bật trigger)** | `.github/workflows/ci-tests.yml` chưa dựng Postgres/Redis ⇒ test DB sẽ SKIP; viết lại để chạy `test_all.py` (service containers). **Giữ `workflow_dispatch`** cho tới khi chủ dự án duyệt | `tests/arch/test_ci_guard.py` vẫn xanh |
| 13 | **Build + chạy thử image Docker** | Dockerfile (non-root, kèm migrations + SDK) chưa từng build. Build, chạy bằng compose DEV, kiểm health + E2E | `docker build` xanh; E2E chạy với container; không đụng Docker project khác |
| 14 | **Quan sát (observability)** | Chỉ có log JSON + `request_id`. Thêm metrics (`/metrics` nội bộ), `traceparent` truyền qua gateway, dashboard mẫu | Test: request_id xuyên gateway→service→feed; metric đếm lỗi/độ trễ |
| 15 | **Retention** | Outbox không xóa; `dead_letters`, `idempotency_keys`, `sessions` hết hạn chưa có job dọn | Job dọn có test; không phá `seq`/cursor |
| 16 | **Registry/doc hygiene** | `status: scaffold` ở registry gây hiểu nhầm (xem 01 §1.6); `docs/architecture/architecture-assessment-v2.md` là bản thiết kế — đối chiếu lại các mục "đề xuất" với thực tế | Doc khớp mã |
| 17 | **Phase 5 (push Redis Streams, tùy chọn)** | Chỉ làm nếu độ trễ của feed kéo không đủ; Postgres vẫn là nguồn bền | Test mất message/đảo thứ tự/Redis chết không mất event |
| 18 | **Phase 6 (cần duyệt)** | `local-ops-hub` → machine credential + OIDC operator + jobs API qua `IntegrationClient`; chuyển vào `apps/local-fallback-tool`; retire legacy | Theo runbook |

## 7.4 Cách chọn việc và định nghĩa "xong"

1. Đọc `CLAUDE.md` + [02](02-trang-thai-tinh-nang.md) để biết cái gì đã/ chưa. 2. Chọn mục backlog (hoặc yêu cầu mới của chủ dự án). 3. Làm theo quy trình ở [06](06-huong-dan-phat-trien.md).
4. **Xong** = triển khai + test tự động xanh (kèm đột biến chứng minh test bắt được lỗi) + số liệu thật + tài liệu cập nhật (cả handbook này, registry, OpenAPI nếu đổi) + `python infra/scripts/test_all.py` xanh + commit cục bộ. 5. Báo cáo trung thực những gì chưa chạy được.

## 7.5 Từ điển

| Thuật ngữ | Nghĩa |
|---|---|
| Strangler | Thay dần legacy bằng service mới, chạy song song, cắt chuyển từng route |
| Canonical | Dạng dữ liệu chuẩn hóa trong `integration_db` (projects/resources/content/metrics/events) với `revision` |
| Read model | Bảng của Analytics dựng từ canonical để truy vấn Dashboard |
| Snapshot | Một lần Tool đẩy dữ liệu; chỉ thành *current* sau commit nguyên tử |
| Outbox / Feed | Bảng sự kiện ghi cùng transaction + endpoint pull `/internal/v1/feed` |
| Seal | Số phiên bản trong PostgreSQL gắn vào khóa cache; tăng cùng transaction đổi số liệu |
| FilterContext | Bộ lọc duy nhất dùng cho mọi KPI/biểu đồ |
| JWKS | Tập khóa công khai Identity publish để service verify token offline |
| `sid` | Session id trong access token, dùng để thu hồi |
| Primary / Fallback Tool | Tool đẩy dữ liệu chính / Tool dự phòng nhận job qua hàng đợi |
| Registry | Platform/Metric Registry: dữ liệu quyết định platform/metric hợp lệ (không phải code) |
| `PENDING_PRODUCTION_APPROVAL` | Nhãn bước production chưa được chủ dự án duyệt — không tự thực hiện |
| `NOT_RUN_EXTERNAL_DEPENDENCY` | Test bị bỏ qua vì cần thứ ngoài repo (vd. ảnh nginx) — chưa kiểm chứng, không phải đạt |
