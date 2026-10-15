# 2. Trạng thái tính năng — đã làm / chưa làm

Chú giải: ✅ xong và có test xanh trên hạ tầng DEV thật · 🟡 làm một phần / có giới hạn đã biết · ⛔ chưa làm · 🔒 chưa làm vì **cần chủ dự án duyệt** (production/hạ tầng).

"Xong" = đã hiện thực **ở repo**, test tự động xanh, chạy thật trên Postgres/Redis DEV riêng. **Chưa có gì được triển khai lên production/VPS.**
Số liệu test tại thời điểm viết: arch 117 · contract 38 · identity 439 · integration 454 · analytics 153 · reward 180 · gateway 452 · e2e 31 · web 147 · SDK 44 · integration-isolation 8.

## 2.1 Theo phase (khớp bảng trong CLAUDE.md §1)

| Phase | Nội dung | Trạng thái | Ghi chú |
|---|---|:-:|---|
| 0 | Tài liệu, baseline (`legacy-baseline` tag), khung service, test kiến trúc | ✅ | Còn: đọc cấu hình nginx thật từ VPS (chỉ đọc) — chưa làm 🔒 |
| 1.0 | api-gateway + `nginx.conf` sinh từ route-map | ✅ repo / 🔒 áp dụng | `nginx -t` bằng nginx thật **chưa chạy** (không có ảnh nginx, không kéo về) |
| 1.1–1.2 | identity: chassis, port auth/accounts/keys, parity legacy, `legacy_sync` (shadow one-way) | ✅ | 7 kịch bản parity với `admin-server` |
| 1.3 | Cutover single-writer Identity (legacy → identity_db) | 🔒 | Quy trình ở `operations/identity-migration-runbook.md`; chưa chạy trên dữ liệu thật |
| 1.4 | RS256/JWKS, phiên `sid` thu hồi được, permission, transactional outbox + feed | ✅ | |
| 1.5 | Reward bỏ bảng `accounts`/JWT secret chung | ✅ (làm ở Phase 4) | Đã chuyển vào `services/reward-service` với skeleton chuẩn (`src/reward_service/…`) |
| 1.6 | OIDC (authorize + PKCE, token, userinfo, introspect, revoke, logout, back-channel) | ✅ | Chưa có Relying Party thật nào dùng (ngoài test) |
| 1.7 | Đăng ký kèm integration + provisioning client tự động | ↩️ thay thế | Gỡ 2026-10-14: Tool cá nhân nay sinh từ capability `tool_integration` (preset `viewer_tool` hoặc admin cấp) — xem `api/integration-api-v1.md` §1-2 |
| 2 | integration-service core | ✅ | |
| 2b | jobs/lease + legacy ACL (26 operation) | ✅ / 🟡 | Thiếu canonicalizer `documents→snapshot` (ingest-contract §7) ⛔🔒 |
| 3 | analytics-service | ✅ | Parity 10 ca với `stats_core` legacy |
| 4 | reward-service dọn coupling | ✅ | |
| 5 | Push (Redis Streams) tùy chọn + DLQ vận hành | ⛔ | Đường mặc định (feed kéo + `dead_letters` + lệnh replay) **đã có**; push chưa cần |
| 6 | Retire legacy, bật lại CI/CD, chuyển `local-ops-hub` | 🔒 | Xem `operations/production-cutover-runbook.md` |
| 7 | admin-web dùng `/v1/*` qua gateway + FilterContext + OIDC tùy chọn | ✅ | |

## 2.2 Theo service

### identity-service
| Tính năng | | Ghi chú |
|---|:-:|---|
| Đăng ký bằng invite key, xác thực email, đăng nhập, refresh (xoay), logout, quên/đặt lại/đổi mật khẩu (mã 6 số), sửa hồ sơ | ✅ | Giữ nguyên contract legacy (`{detail}`) |
| Admin: danh sách/khóa/sửa/xóa user; invite key (role, max_uses, hết hạn) | ✅ | |
| Preset trên invite key (`viewer/viewer_tool/dash_admin/admin/discord` → role + capability); `PATCH /v1/admin/keys/{key}`; `GET /v1/admin/keys/presets` | ✅ | Thay thế `integration_policy`/đăng ký kèm Tool (gỡ 2026-10-14) — khoá Tool cá nhân nay sinh từ capability `tool_integration` (integration-service) |
| Token RS256 + JWKS + xoay khóa (`python -m identity_service.keys rotate`), khóa ký mã hóa Fernet trong DB | ✅ | |
| Phiên `sid`, thu hồi khi logout/khóa/đổi mật khẩu/đổi role | ✅ | Service khác verify offline ⇒ độ trễ thu hồi ≤ TTL token + feed `session.revoked` |
| Permission catalog + cấp/thu (`reward:access`, `reward:admin`…), shim role `discord`→`reward:access` | ✅ | |
| OIDC provider + hosted login + CSRF + consent (third-party) + back-channel logout (chống SSRF) | ✅ | Consent không được ghi nhớ; refresh trượt (chưa hạn tuyệt đối cho family) |
| Outbox + `/internal/v1/feed` (khóa theo bên đọc) | ✅ | |
| `legacy_sync` shadow (legacy → identity_db) + kiểm parity | ✅ | Chưa chạy trên dữ liệu production 🔒 |
| Gửi email thật (SMTP) | 🟡 | Adapter SMTP có; DEV dùng `LogMailer` (in token ra log khi `IDENTITY_ENV=dev`). Chưa cấu hình SMTP thật |
| TTL access token 600 s (quyết định D12) | 🟡 | Hiện **900 s**; test parity khóa giá trị 900. Đổi qua `IDENTITY_ACCESS_TOKEN_TTL_S` + sửa test có chủ đích |
| Thao tác nhạy cảm dùng introspection fail-closed (D13) | 🟡 | Endpoint `/oauth/introspect` có; **chưa service nào gọi** cho thao tác nhạy cảm |

### integration-service
| Tính năng | | Ghi chú |
|---|:-:|---|
| Client / credential (`mpk_<key_id>.<secret>`, lưu hash, hiện 1 lần, xoay, thu hồi, TTL tùy chọn), scope | ✅ | Client PATCH chỉ admin |
| Tool cá nhân (`ownership=personal`) tự sinh từ `capability:tool_integration`, khoá chờ chủ nhận (`/me/credential/reveal`, Fernet, hết hạn 7 ngày); thu hồi capability ⇒ tạm dừng + thu hồi khoá | ✅ | Admin cấp/xoay hộ không thấy secret; chuyển chủ `POST /clients/{id}/transfer` (dữ liệu cũ giữ owner cũ) |
| Công cụ dùng chung (`ownership=shared`): wizard admin `draft → config → activate → cấp khoá`; ghi dữ liệu bắt buộc `X-Delegated-Job` | ✅ | |
| Connection guide `/me/connection-guide` + `/me/openapi.json` (endpoint theo scope khoá, schema đã resolve, mã lỗi, snapshot + rejections gần nhất) | ✅ | Cần `INTEGRATION_PUBLIC_BASE_URL` để hiện base URL |
| Handshake + installation; **device binding** 1 khoá : 1 thiết bị, `X-Installation-Id` bắt buộc ngoài handshake | ✅ | `installation_id` không phải bí mật (giới hạn ghi ở integration-api §8) |
| Platform/Metric Registry (dữ liệu) + admin PUT | ✅ | Thêm platform = dòng dữ liệu |
| Project (`PUT /projects/{key}`), resource batch, resource ↔ project | ✅ | |
| Snapshot: tạo → tải lô → commit nguyên tử; `expected_counts`, `accept_partial`, rejections, state machine, `current` | ✅ | Full snapshot chỉ suy luận `removed` trong phạm vi owner+client+platform |
| Idempotency-Key (mọi endpoint, tùy chọn) | ✅ | Chỉ lưu response thành công, phát lại đúng byte |
| Outbox + feed `snapshot.committed`, `resource.changed`, `credential.revoked`; canonical export `/internal/v1/canonical/{kind}` + `/summary` (checksum) | ✅ | Revision cấp dưới advisory lock |
| Vòng đời client theo account (`account.verified/status_changed/deleted/permission_changed`) + dead-letter, replay | ✅ | |
| Jobs/lease cho Fallback Tool (claim `SKIP LOCKED`, lease ≤900s, heartbeat, retry/backoff, `dead`) | ✅ | Không phát event `job.*` (hợp đồng feed chưa có) |
| Legacy ACL: `/v1/resources/*`, `/v1/ops/*`, `/v1/viewer/links*` | ✅ | Một đường ghi (cùng canonical). Khác biệt có chủ đích: `owner*` trong body → 422 |
| Canonicalizer `documents → snapshot` + DB role `legacy_ingest_writer` | ⛔🔒 | Cần thay đổi hạ tầng và sync server |
| Rate limit mặt phẳng user | ⛔ | Rate limit máy/pairing có, trong bộ nhớ từng worker |
| Cache checksum summary khi bảng rất lớn | ⛔ | Hiện tính SQL toàn bảng |

### analytics-service
| Tính năng | | Ghi chú |
|---|:-:|---|
| Consumer kéo canonical theo `revision` (tất cả kind cùng head, một transaction, kèm `processed_events`/`feed_cursor`/seal) | ✅ | Mất event vẫn đuổi kịp; `rebuild` nguyên tử; `reconcile` bằng checksum |
| Endpoint: summary, traffic, top-channels, views-by-country, channel-breakdown, country-tags, platform-tags, metrics, compare/{platform,owner,country,channel,group,project}, freshness, groups, admin (rebuild/reconcile/sync/dead-letters/cache/country-*) | ✅ | Alias `/v1/viewer/*`, `/v1/admin/country-*` giữ shape legacy |
| FilterContext duy nhất (owner, platform, country, project, group, platform_tag, date_from, date_to) | ✅ | Biểu đồ quốc gia **không** bị thu hẹp bởi `country` (`ignored_filters`) |
| Cache-Aside Redis (seal ở PostgreSQL, khóa chứa scope, TTL, Redis chết → PG) | ✅ | |
| Freshness (`as_of`, `lag_seconds`, `stale`) | ✅ | |
| Bảng tổng hợp `agg_daily` | ⛔ | Khai báo ở registry, chưa dùng; truy vấn tính từ read model |
| Single-flight chống thundering herd; seal theo owner | ⛔ | Seal đang toàn cục (an toàn, hit rate thấp hơn) |
| PK `read_resources` gồm `resource_type` | ⛔ | Hiện hai loại resource trùng `external_id` trong cùng platform/owner sẽ ghi đè nhau |

### api-gateway
| Tính năng | | Ghi chú |
|---|:-:|---|
| Proxy ASGI streaming, route theo `route-map.json` + `public_prefixes`, longest-prefix | ✅ | |
| Chặn `/internal` mọi biến thể; strip header nhạy cảm; chuẩn hóa path | ✅ | |
| CORS allowlist, security headers, giới hạn body/header/URL, rate limit (strict/auth/default), circuit breaker, access log đã che secret | ✅ | Trạng thái limiter/breaker **trong bộ nhớ từng tiến trình** |
| `render_nginx.py` + `nginx.conf` sinh từ nguồn | ✅ / 🟡 | `nginx -t` thật chưa chạy 🔒 |
| WebSocket / HTTP upgrade | ⛔ | Bị từ chối |

### reward-service
| Tính năng | | Ghi chú |
|---|:-:|---|
| Engine job slash-command (SSE, recovery, reconcile, token pool) | ✅ | Giữ nguyên, `--workers 1` |
| DB riêng `reward_db` (migration 001→005), không còn `accounts`/`invite_keys`/`refresh_tokens` | ✅ | |
| Verify RS256 qua `manager_sdk` (JWKS offline); `reward:access`/`reward:admin` | ✅ | |
| Quyền do Identity quản lý (`grant-discord`/`revoke-discord` gọi Identity bằng Bearer của người gọi) | ✅ | Revoke role `discord` (shim) → 409 cho tới khi cấp `reward:access` tường minh |
| Consumer feed Identity → `account_projection`, `account_denylist`; thu quyền/khóa/xóa/`session.revoked` dừng job | ✅ | `session.revoked` dừng **mọi** job của tài khoản (thận trọng) |
| Chuyển vào `services/` + skeleton chuẩn (`src/reward_service`, `migrations/`, Dockerfile) | ✅ | Image build được, health xanh trong `infra/compose/stack.yml` |
| `deploy-reward.yml` theo mô hình mới | ⛔🔒 | Hiện theo mô hình cũ (Postgres chung, `JWT_SECRET`) |

### manager-web (`apps/manager-web/`)
| Tính năng | | Ghi chú |
|---|:-:|---|
| Chuyển sang `/v1/*` qua gateway; lõi HTTP (refresh 401 gộp một lần, timeout, abort) | ✅ | |
| FilterContext duy nhất đồng bộ URL; hủy request cũ; banner stale/unavailable | ✅ | |
| Đăng nhập: legacy hoặc OIDC PKCE (`VITE_AUTH_MODE`); refresh token **không** vào `localStorage` | ✅ | OIDC mới test bằng mock, chưa với Identity thật qua trình duyệt |
| Trang quản trị: invite key (preset), permission dialog, Integration Clients (API key hiện 1 lần) | ✅ | |
| Quét bundle không còn secret (`postbuild`) | ✅ | |
| Thử trên trình duyệt thật với stack thật | ⛔ | Test hiện bằng jsdom + mock `fetch` |
| Banner 503 riêng cho Reward/Integration; tách chunk bundle (~1 MB) | ⛔ | |
| Nâng major vite/vitest (5 lỗ hổng `npm audit` dev-only) | ⛔🔒 | Cần quyết định riêng |

### Khác
| Hạng mục | | Ghi chú |
|---|:-:|---|
| `manager_sdk.IntegrationClient` (idempotency ổn định, retry an toàn, chia lô 500, không rò key) | ✅ | Chưa có `heartbeat`/`lease_token` |
| SDK TypeScript (`packages/sdk-typescript`) | ⛔ | CLAUDE.md ghi "chưa có" |
| `apps/local-fallback-tool/` chuyển sang machine credential + jobs API (Fallback Tool) | ⛔🔒 | Thư mục đã đổi tên (không còn `local-ops-hub`); codebase vẫn là legacy nguyên trạng |
| Test E2E tiến trình thật + cô lập lỗi + Mock Tool X | ✅ | `tests/e2e` |
| Cổng chất lượng `test_all.py` (bootstrap hạ tầng, migrate, mọi bộ, frontend) | ✅ | |
| CI GitHub | 🟡 | `ci-tests.yml` chỉ `workflow_dispatch`, **chưa cập nhật** cho bộ test mới (không dựng Postgres ⇒ test DB sẽ SKIP). Deploy workflows chỉ `workflow_dispatch` (khóa bằng `tests/arch/test_ci_guard.py`) |
| Build Docker image các service + chạy stack bằng container | ✅ | `infra/compose/stack.yml`: 5 image build được, `/health/ready` 200, gateway chuyển được JWKS. Chưa chạy E2E bằng container |
| Production / VPS | 🔒 | Không có gì được triển khai |

## 2.3 Khác biệt có chủ đích so với legacy (đừng "sửa" lại)

1. `owner*` trong body ingest/legacy → `422` (legacy lặng lẽ bỏ qua). 2. Lỗi 422 không phản chiếu input. 3. KPI `managed/active_channels` đếm cả kênh YouTube; bộ lọc áp cả `videos_posted`; ngày mặc định 30 ngày; ngày sai định dạng → 422.
4. Nhập lại cùng `input_ref` + `resource_type` trả resource cũ (legacy tạo bản trùng). 5. `PATCH resource_type` chỉ đổi thuộc tính hiển thị, khóa canonical giữ nguyên. 6. "Ghi nhớ đăng nhập" ở web chỉ giữ trong tab (`sessionStorage`). 7. Reward: người bị thu quyền/khóa bị chặn cả route đọc.
8. Identity: mật khẩu >72 byte (UTF-8) bị `422` ở register/reset/đổi mật khẩu (bcrypt chỉ xét 72 byte; bcrypt 5 ném lỗi thay vì cắt); đăng nhập vẫn cắt 72 byte để hash legacy dài còn dùng được. 9. Identity: chỉ role `admin` mới khóa/sửa/đổi role/xóa được tài khoản `admin` (dash_admin → `403`, `code=ADMIN_TARGET_FORBIDDEN`; chặn chiếm admin qua forgot-password); thao tác lên user thường giữ nguyên. 10. OIDC: access token của client KHÔNG first_party chỉ có `aud` riêng (audience của client, mặc định `client_id`), không có `manager-api` nên API nền tảng từ chối; userinfo/introspect/revoke vẫn nhận. Client first_party giữ `aud=[manager-api, ...]`. 11. Đổi email qua `PATCH /v1/auth/profile` đặt `email_verified=false` và gửi lại link xác thực tới email mới (status giữ nguyên, vẫn đăng nhập được); phát `account.profile_updated`.
