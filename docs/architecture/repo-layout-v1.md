# Repo Layout v1 — chuẩn tổ chức thư mục (ĐỀ XUẤT, chờ duyệt phần di chuyển)

Trạng thái: **đã rà soát và thiết kế; CHƯA di chuyển thư mục nào.** Lý do: việc di chuyển động tới thư mục legacy đang chạy production và các workflow deploy; cần người dùng duyệt rõ ràng (xem §7).
Phần không cần di chuyển (test chuẩn skeleton service, tài liệu này) đã có hiệu lực ngay.

## 1. Rà soát hiện trạng — vấn đề

| # | Vấn đề | Bằng chứng |
|---|---|---|
| 1 | **Ba "thế hệ" cùng nằm ở gốc repo**: legacy (`admin-server/`, `admin-web/`, `local-ops-hub/`, `reward-service/`) cạnh mã mới (`services/`, `packages/`, `infra/`) | `ls` gốc; 4 thư mục legacy + 5 thư mục mới |
| 2 | **Không có chỗ cho ứng dụng frontend/tool**: CLAUDE.md nói `apps/`, thư mục này không tồn tại; web và tool nằm ở gốc | `admin-web/`, `local-ops-hub/` |
| 3 | **Reward là service nhưng nằm ngoài `services/`**; cấu trúc phẳng (không `src/`, không `Dockerfile`, không `.env.example` đạt chuẩn), đặt ngoại lệ trong registry (`legacy-in-place`) | `reward-service/` |
| 4 | **Hai bộ quy ước service**: 4 service mới theo skeleton chuẩn, reward/admin-server theo cấu trúc cũ | `services/*` vs gốc |
| 5 | **Tài liệu gốc lỗi thời**: `README.md` (mô tả "2 thành phần" admin-server/admin-web), `DEPLOY.md` (deploy admin-server bằng pm2 + "push lên main → tự deploy") | gốc repo |
| 6 | **Script rải rác**: `scripts/setup-vps.sh` (gốc) tách biệt với `infra/scripts/` | `scripts/` |
| 7 | **Triển khai chưa có chỗ ở**: chỉ có compose dữ liệu DEV; không có thư mục cho manifest deploy theo môi trường, không có compose toàn stack | `infra/docker/` |
| 8 | **Ngữ cảnh agent/tác vụ nằm ngoài `docs/`**: `.omp/` (mô tả legacy, công cụ agent dùng), `tasks/` (prompt/backlog) | gốc repo |
| 9 | Đường dẫn legacy bị **cứng ở nhiều nơi** (workflow, script, test parity, registry) ⇒ di chuyển phải làm có kiểm soát | §5 |

Phần đã ổn (giữ nguyên): `services/<x>-service/` (skeleton thống nhất: `src/<package>/<module>/`, `migrations/`, `tests/`, `Dockerfile`, `.env.example`, `README.md`, `pytest.ini`, `requirements.txt`), `packages/`, `tests/{arch,contract,integration,e2e}`, `infra/{gateway,docker,scripts,service-registry.json}`, `docs/{api,architecture,operations,handbook}`.

## 2. Cấu trúc đích

```text
/
├─ CLAUDE.md                     luật ràng buộc
├─ README.md                     giới thiệu nền tảng + trỏ tới handbook (viết lại, thay bản legacy)
├─ apps/                         ỨNG DỤNG phía người dùng/biên (không phải backend nghiệp vụ)
│  ├─ manager-web/               ← admin-web/            (React/Vite)
│  └─ local-fallback-tool/       ← local-ops-hub/        (Tool B, máy cục bộ)
├─ services/                     BACKEND — mỗi thư mục một service độc lập, cùng một skeleton
│  ├─ api-gateway/  identity-service/  integration-service/  analytics-service/
│  └─ reward-service/            ← reward-service/       (sau khi chuẩn hóa skeleton — §4)
├─ legacy/                       HỆ CŨ ĐÓNG BĂNG (chỉ sửa lỗi nghiêm trọng; mỗi thư mục có README ghi "đóng băng")
│  └─ admin-server/              ← admin-server/
├─ packages/                     mã dùng chung CHỈ generic: contracts/, observability/, sdk-python/, (sdk-typescript/ tương lai)
├─ infra/
│  ├─ service-registry.json      nguồn sự thật service/module/bảng/event/cổng
│  ├─ gateway/                   route-map.json, render_nginx.py, nginx.conf
│  ├─ docker/                    hạ tầng dữ liệu DEV (compose.dev.yml, templates/)
│  ├─ compose/                   [MỚI] stack.yml toàn nền tảng (build mọi service từ Dockerfile, cấu hình qua env_file)
│  ├─ deploy/                    [MỚI] manifest theo môi trường; legacy/ chứa setup-vps.sh (← scripts/setup-vps.sh). Mọi thứ production: PENDING_PRODUCTION_APPROVAL
│  └─ scripts/                   bootstrap_dev, devenv, dev_stack, scaffold_service, test_all
├─ tests/                        arch/, contract/, integration/, e2e/ (xuyên service)
├─ docs/                         api/, architecture/, operations/, handbook/   (DEPLOY.md legacy → operations/legacy-vps-deploy.md)
├─ tasks/                        prompt/backlog của chủ dự án — GIỮ NGUYÊN (bị tham chiếu bằng đường dẫn)
└─ .omp/                         ngữ cảnh công cụ agent về hệ legacy — GIỮ NGUYÊN, thêm bảng ánh xạ đường dẫn mới
```

Quy tắc đặt chỗ (đưa vào CLAUDE.md khi áp dụng):

| Là gì | Đặt ở | Tiêu chí |
|---|---|---|
| Backend có API/DB/event riêng | `services/<tên>-service` | có trong `service-registry.json`; một DB + một DB user |
| Giao diện, CLI/Tool chạy ở máy người dùng, website khác | `apps/<tên>` | không sở hữu dữ liệu nghiệp vụ; nói chuyện với hệ thống qua gateway/SDK |
| Mã tái sử dụng | `packages/<tên>` | generic, không logic nghiệp vụ, không import service |
| Hạ tầng, deploy, cấu hình môi trường | `infra/…` | không chứa mã nghiệp vụ |
| Test xuyên nhiều service | `tests/<loại>` | test của một service nằm trong service đó |
| Hệ cũ chưa gỡ | `legacy/<tên>` | đóng băng; không thêm tính năng |

## 3. Chuẩn skeleton (áp dụng NGAY — có test `tests/arch/test_repo_layout.py`)

**Mỗi `services/<x>-service/` bắt buộc có:** `README.md`, `Dockerfile`, `.env.example`, `requirements.txt`, `pytest.ini`, `src/<package>/main.py`, `tests/`; thêm `migrations/` **nếu và chỉ nếu** service có DB (registry `database != null`).
Service DB-less (gateway) không có `migrations/`. `.env.example` không chứa secret thật; biến có tiền tố `<TÊN>_`.

**Mỗi `apps/<x>/` (sau khi chuyển) bắt buộc có:** `README.md`, `package.json` (hoặc `pyproject.toml`), `.env.example` nếu có cấu hình, script `typecheck`/`test`/`build` (frontend).

**Docker:** `Dockerfile` ở gốc service, **build context = gốc repo** (để COPY `packages/`), chạy non-root, có HEALTHCHECK `/health/live`, migration chạy bằng job riêng (`python -m <package>.migrate`), cấu hình hoàn toàn qua env.
**Cấu hình:** một `.env.example` mỗi service; secret DEV do `bootstrap_dev.py` sinh vào `.env.dev` (gitignored); production dùng secret manager. Không file `.env` chung giữa service.
**Triển khai:** mỗi service độc lập (build/deploy riêng); `infra/compose/stack.yml` chỉ là cách chạy cả bộ, không phải điều kiện của từng service.

## 4. Thêm service / app mới — không ảnh hưởng cái đã có

| Việc | Các bước (chỉ thêm, không sửa thư mục khác) |
|---|---|
| Service mới | 1) khai trong `infra/service-registry.json` (`status: scaffold`, cổng/DB/package/`public_prefixes`) 2) `python infra/scripts/scaffold_service.py` (tạo đúng skeleton §3) 3) thêm DB+user vào `infra/docker/templates/initdb/01-databases.sql.tpl` 4) route-map nếu có route legacy → `render_nginx.py` 5) thêm vào `DevStack` nếu cần E2E. Test arch kiểm skeleton + ranh giới |
| App mới | `apps/<tên>/` với README + package.json + `.env.example`; gọi hệ thống qua gateway/SDK; thêm bước vào `test_all.py` |
| Website dùng đăng nhập chung | đăng ký OIDC client trong Identity (không sửa service nào) |
| Tool mới | không sửa Manager (cấp credential + tài liệu) |

`reward-service` cần **chuẩn hóa skeleton** khi chuyển vào `services/` (đang phẳng): `src/reward_service/…`, `Dockerfile`, `pytest.ini`… — việc này đổi nhiều import nên tách thành bước riêng (Bước 3 ở §6), không gộp với di chuyển thư mục.

## 5. Bản đồ di chuyển và các đường dẫn bị cứng

| Từ | Đến |
|---|---|
| `admin-web/` | `apps/manager-web/` |
| `local-ops-hub/` | `apps/local-fallback-tool/` |
| `admin-server/` | `legacy/admin-server/` |
| `reward-service/` | `services/reward-service/` |
| `DEPLOY.md` | `docs/operations/legacy-vps-deploy.md` |
| `scripts/setup-vps.sh` | `infra/deploy/legacy/setup-vps.sh` |

Chạm tới (đã kiểm kê bằng `git grep`; tên dạng *khái niệm* "admin-server" trong docstring không cần sửa):

- **Workflow** (`.github/workflows/deploy.yml`, `deploy-reward.yml`: `working-directory`, `rsync`, `cache-dependency-path`, comment `paths:`); đều đang `workflow_dispatch` nhưng sẽ hỏng khi bật lại nếu không sửa. **Đường dẫn trên VPS (`/srv/admin`, `/srv/reward`) không đổi.**
- **Script/hạ tầng:** `infra/scripts/{test_all,dev_stack,scaffold_service}.py`, `scripts/setup-vps.sh`, `infra/service-registry.json` (`reward-service.path`, bỏ `target_path`), `.gitignore` (mục `admin-web`).
- **Test dựa đường dẫn:** `tests/arch/{test_boundaries,test_infra}.py`, `tests/contract/test_{analytics_stats,identity_legacy}_parity.py` (chèn `admin-server` vào `sys.path`), `tests/e2e/test_mock_tool_x.py`, `reward-service/tests/*` (**các phép `parents[N]` tính gốc repo phải +1**), `reward-service/scripts/check-env.py`, `reward-service/sdk_path.py`, `services/*/tests` có đọc `docs/api/legacy` (không đổi).
- **Tài liệu:** `CLAUDE.md`, `docs/**` (handbook, runbook, báo cáo), `.omp/**` (thêm bảng ánh xạ, không viết lại), `README`.
- **Không đụng:** `tasks/` (prompt lịch sử của chủ dự án), `docs/api/legacy/*.json` (baseline OpenAPI), tên package Python, tên service/registry key, route.
- **File ignore nằm trong thư mục:** `admin-server/admin-auth.db` (chứa hash mật khẩu), `node_modules/`, `__pycache__/` — phải di chuyển cả thư mục bằng lệnh hệ điều hành (không `git mv` từng file) để không bỏ lại.

## 6. Kế hoạch thực hiện (mỗi bước: test xanh rồi mới commit cục bộ)

1. **Di chuyển thư mục** (`admin-web`, `local-ops-hub`, `admin-server`, `reward-service`) + `DEPLOY.md`, `scripts/setup-vps.sh`; sửa mọi đường dẫn ở §5 (quy tắc thay thế có lookbehind để không nhân đôi tiền tố); registry + `render_nginx --check`.
2. **Gốc repo:** viết lại `README.md`; thêm `legacy/admin-server/README.md` ("ĐÓNG BĂNG") và bảng ánh xạ ở `.omp/AGENTS.md`; cập nhật CLAUDE.md §3 (cấu trúc), handbook 01 §1.5, 06.
3. **Chuẩn hóa skeleton reward-service** (`src/reward_service/`, `Dockerfile`, `pytest.ini`, `.env.example`) — chạy lại 180 test.
4. **Triển khai:** `infra/compose/stack.yml` (+ README) và `infra/deploy/` (manifest rỗng có tài liệu); build thử image bằng compose DEV.
5. **Siết kiểm tra:** mở rộng `tests/arch/test_repo_layout.py` thêm: tập thư mục gốc cho phép, `apps/*` skeleton, mọi đường dẫn trong registry tồn tại, `legacy/*` có README đóng băng.
6. Chạy `python infra/scripts/test_all.py --legacy` đầy đủ; cập nhật `implementation-final-report.md`.

Kiểm chứng sau di chuyển: `tests/arch` (đường dẫn registry/ranh giới), `tests/contract` (parity import legacy), `reward` 180 test, `legacy` 6 test, web typecheck/test/build, E2E 31 test, `render_nginx --check`.

## 7. Rủi ro và vì sao cần duyệt

- Thư mục legacy đang chạy production (`ufadmin`, `ufreward` qua pm2; `deploy.yml` rsync theo đường dẫn repo). Đổi đường dẫn trong repo **không** đổi gì trên VPS, nhưng workflow phải được sửa **đúng** trước khi ai bật lại CI/CD — sai sót sẽ lộ ra lúc deploy thật.
- `reward-service` đổi vị trí kéo theo import/`parents[N]` (180 test sẽ báo ngay nếu sót).
- Sự cố kiểu "ghi đè" đã từng xảy ra trên máy này với tài nguyên dùng chung; việc di chuyển động tới `admin-auth.db` (chứa dữ liệu tài khoản) nên có bản sao lưu trước.
- Đảo ngược: tất cả là di chuyển + sửa text trong git, hoàn tác bằng `git revert` (trừ các file ignored — đã nằm trong thư mục được di chuyển nguyên vẹn).
