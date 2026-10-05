# CLAUDE.md — Manager Platform (monorepo microservices)

Tài liệu RÀNG BUỘC. Mọi thay đổi về sau phải tuân theo. Vi phạm sẽ làm `python infra/scripts/test_all.py` đỏ.
Đọc trước khi làm: `docs/architecture/architecture-assessment-v2.md` (kiến trúc, quyết định D1-D14) và
`docs/api/ingest-data-contract-v1.md` (hợp đồng dữ liệu Tool đẩy lên).

## 1. Trạng thái

Đang tái cấu trúc hệ Admin Manager cũ thành các service độc lập theo **Strangler Pattern** (không big-bang).
Làm việc trên nhánh `refactor/microservices`. Tag `legacy-baseline` = code trước khi tái cấu trúc.

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 0 | Freeze: tài liệu, baseline, khung service, test kiến trúc | Xong phần repo. **Còn:** nginx thật từ VPS (chỉ đọc) |
| 1.0 | Gateway pass-through | Chưa, **cần duyệt riêng** |
| 1.1 | identity-service chassis | Khung xong (health, config, test). Chưa có DB |
| 1.2 → 1.7 | Identity: auth, cutover single-writer, RS256/JWKS, Reward bỏ `accounts`, OIDC, đăng ký kèm integration | Chưa |
| 2 / 3 / 4 / 5 / 6 | Integration / Analytics / Reward dọn / push + DLQ / retire legacy | Chưa |

Legacy còn chạy: `admin-server/`, `reward-service/`, `admin-web/`, `local-ops-hub/`. **Không sửa legacy** trừ lỗi nghiêm
trọng; tính năng mới làm ở service đích. `reward-service/` được `git mv` sang `services/` ở Phase 1.5.

## 2. Luật không được phá

1. **Không service nào import code của service khác** hay module legacy (`auth`, `db`, `stats_core`, ...). Chỉ được
   dùng `manager_contracts` và `manager_observability` (package dùng chung, chỉ generic).
2. **Không đọc/ghi/JOIN bảng của service khác.** Mỗi service một database và một DB user riêng.
3. Cần dữ liệu của service khác: gọi API công khai của nó hoặc nhận event. Không có đường tắt.
4. **Không hardcode tên Tool/project** (ITL, Sible...) ở bất kỳ đâu. Tên Tool chỉ là metadata.
5. **Không endpoint riêng theo Tool.** Dùng namespace chung `/v1/integrations/*`.
6. **Ownership suy ra từ credential**, không tin `owner_account_id` do Tool gửi.
7. **Machine credential ≠ user credential.** Tool dùng `client_id + API key + scopes`; không dùng mật khẩu user hay
   JWT web. **Role (user) và Scope (machine) là hai mô hình độc lập.**
8. **Không dùng một JWT secret đối xứng chung.** Token ký RS256/EdDSA, service verify bằng JWKS (offline).
9. **Không chuỗi sync dài giữa service.** Tối đa 1 hop gateway → service. Liên service dùng event/queue.
10. `/health/ready` chỉ kiểm tài nguyên của CHÍNH service, không gọi service khác hay Redis.
11. **Không breaking change âm thầm.** Breaking = thư mục contract version mới; contract `legacy` đóng băng.
12. **Dashboard không đọc payload thô của Tool**; chỉ đọc read model dựng từ snapshot đã commit.
13. Không đặt logic nghiệp vụ vào package dùng chung.

## 3. Cấu trúc

```text
infra/service-registry.json   NGUỒN SỰ THẬT: service, module, bảng sở hữu, event, cổng, DB
infra/gateway/route-map.json  Ánh xạ route legacy → service đích
infra/docker/                 compose.dev.yml, initdb (DB + user riêng), redis ACL
infra/scripts/                scaffold_service.py, test_all.py
services/<name>-service/      src/<package>/<module>/, migrations/, tests/, Dockerfile, .env.example
packages/contracts/           schema hợp đồng (ingest/<ngày>/) + python/manager_contracts
packages/observability/       log JSON, request-id, health, app factory
packages/sdk-typescript|sdk-python/   SDK sinh từ OpenAPI (chưa có)
apps/                         manager-web, local-fallback-tool (chuyển vào ở phase tương ứng)
tests/{arch,contract,integration,e2e}/
docs/architecture, docs/api (docs/api/legacy = baseline OpenAPI cũ)
```

Mỗi service: module theo **chức năng** (xem `modules` trong registry). Mỗi module có docstring đầu `__init__.py` khai
`module / service / phase / status / responsibility`. Mỗi service có chassis chuẩn: `/health/live|ready|version`,
`/openapi.json`, envelope lỗi ổn định, log JSON, config fail-fast (`<TÊN>_*` env), Dockerfile, migration riêng, test riêng.

## 4. Quy trình thay đổi (làm đúng thứ tự)

| Việc | Các bước |
|---|---|
| Thêm module | Sửa `modules` trong registry → `python infra/scripts/scaffold_service.py` → viết code + test trong module |
| Thêm service | Thêm vào registry (`status: scaffold`, cổng/DB/package mới) → scaffold → thêm DB + user vào `initdb` → cập nhật `route-map.json` nếu có route legacy |
| Thêm bảng | Thêm vào `owned_tables` của đúng service → migration `NNNN_ten.sql` (forward-only) trong `services/<s>/migrations/` |
| Thêm event | Khai ở `emits` của service phát và `consumes` của service nhận; event có `id`, `type`, `data_version`; consumer idempotent theo `id` |
| Thêm endpoint | Request/response schema, `error.code` ổn định, scope/role, idempotency (mutation), ví dụ, OpenAPI; test contract |
| Thêm metric canonical | Thêm vào Metric Registry (dữ liệu) và schema ingest; không thêm cột tùy tiện |
| Thêm Tool mới | **Không sửa Manager.** Cấp credential + đưa tài liệu API. Nếu phải sửa Manager chỉ để nhận tên Tool thì ranh giới đang sai |
| Thêm website dùng đăng nhập chung | Đăng ký OIDC client trong Identity; xem mục 2.9 của assessment |

## 5. Dữ liệu, cache, sự kiện

- Snapshot chỉ thành *current* sau commit atomic; building/failed không thay snapshot thành công gần nhất.
- Ingest: upsert theo khóa tự nhiên, **bản có `observed_at` mới hơn thắng**, metric là số tuyệt đối (ghi đè, không cộng);
  `null` ≠ `0`; không bỏ sót im lặng (bản ghi sai trả về trong `rejections`).
- Cache = Redis **Cache-Aside**: đọc HIT/MISS→DB→SET (luôn có TTL); ghi = commit PostgreSQL **rồi mới** `DEL`/tăng seal phiên bản.
  Khóa cache chứa scope người gọi + Filter Context. Redis chết thì rơi về PostgreSQL. Mỗi service ACL + prefix riêng.
- Event: transactional outbox trong DB là nguồn bền; change feed (pull) là đường mặc định, push (Redis Streams) là tùy chọn.
- Mọi KPI và biểu đồ dùng chung **một FilterContext**. So sánh quốc gia nằm trong biểu đồ riêng.

## 6. Kiểm thử — cổng chất lượng

```bash
pip install -r tests/arch/requirements.txt
python infra/scripts/test_all.py            # tất cả (arch, contract, packages, từng service, integration)
python infra/scripts/test_all.py --fast     # bỏ test tiến trình thật (~25s)
python infra/scripts/test_all.py --legacy   # thêm test admin-server cũ
```

- Mỗi service chạy pytest **riêng** (`cd services/<s> && python -m pytest -q`), không gộp một lần.
- Mỗi thay đổi hành vi phải có test; mỗi quy tắc kiến trúc phải có "test của test" (ca giả lập vi phạm).
- **SKIP không phải là PASS.** Test cần Postgres/Redis (`requires_postgres`) phải báo SKIP rõ lý do và không được tính là đã kiểm chứng.
- Không sửa/xóa test kiến trúc để cho qua. Muốn đổi quy tắc: đổi registry/ADR có chủ đích, giải thích trong commit.
- Báo cáo trung thực: test đỏ thì nói đỏ; bước chưa chạy được (không có Docker/Postgres) thì nói chưa chạy.

## 7. Git và CI/CD

- **CHỈ commit cục bộ. KHÔNG `git push`** cho tới khi người dùng nói hoàn thiện. Không commit lên `main`.
- Workflow deploy đã tắt trigger `push` (chỉ còn `workflow_dispatch`); `tests/arch/test_ci_guard.py` giữ điều đó.
  Bật lại deploy chỉ ở Phase 6, theo yêu cầu rõ ràng của người dùng, cùng commit sửa test guard.
- Không commit `.env`, `*.db`, secret. Mật khẩu trong `infra/docker` chỉ là giá trị dev.
- Commit nhỏ theo bước, thông điệp nói rõ phase và phạm vi.

## 8. Quy ước

- Python 3.12, FastAPI, type hints, `ruff`. Mã lỗi, tên field, đường dẫn API: tiếng Anh. Docstring/comment/tài liệu: tiếng Việt (khớp codebase).
- Không log mật khẩu, token, API key. Lỗi validation không phản chiếu giá trị đầu vào.
- Định nghĩa "xong" của một phase: có triển khai, có test tự động xanh, có số liệu thật báo cáo, tài liệu cập nhật, commit cục bộ.
- `.omp/context/` mô tả hệ legacy (vẫn đúng cho code cũ). Kiến trúc đích lấy theo tài liệu này và `docs/architecture/`.
