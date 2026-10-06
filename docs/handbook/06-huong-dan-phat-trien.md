# 6. Hướng dẫn phát triển

Quy trình thay đổi chuẩn là bảng ở `CLAUDE.md` §4. Tài liệu này bổ sung **cách làm cụ thể**, lệnh, và các bẫy đã gặp.

## 6.1 Dựng môi trường

Yêu cầu: Python 3.12, Node.js (cho `admin-web`), Docker (chỉ cho Postgres/Redis DEV riêng).

```bash
pip install -r tests/arch/requirements.txt                       # + pip install -r services/<x>/requirements.txt khi làm service đó
python infra/scripts/bootstrap_dev.py                            # .env.dev ngẫu nhiên + .dev/ (gitignored). --check / --rotate
docker compose --env-file .env.dev -f infra/docker/compose.dev.yml up -d
python infra/scripts/test_all.py                                 # cổng chất lượng đầy đủ (bootstrap lại mọi thứ còn thiếu, chạy ~25-30 phút)
```

`test_all.py` tự: sinh `.env.dev` nếu thiếu, `pip install` thư viện thiếu, `docker compose up` (stack DEV riêng), migrate, chạy các bộ: `arch, contract, pkg:*, svc:*, integration, e2e, web:{install?,typecheck,test,build}`.
Thoát khác 0 nếu bất kỳ bước/bộ nào đỏ. SKIP vì phụ thuộc ngoài (`NOT_RUN_EXTERNAL_DEPENDENCY`, hiện chỉ `nginx -t`) được liệt kê là "chưa kiểm chứng" nhưng không làm đỏ; **mọi SKIP khác làm đỏ** bản đầy đủ.
Tùy chọn: `--fast` (bỏ integration/e2e/frontend), `--legacy` (thêm admin-server), `--only <chuỗi>`, `--no-bootstrap`.

Chạy từng phần:
```bash
cd services/identity-service && python -m pytest -q          # mỗi service một tiến trình pytest riêng (không gộp)
cd admin-web && npm run typecheck && npm test && npm run build
python -m pytest tests/arch -q                                # luôn chạy sau khi đổi registry/ranh giới/hạ tầng
python infra/scripts/dev_stack.py up                          # 5 service bằng tiến trình thật, cổng ngẫu nhiên in ra màn hình
```

Mỗi service tự đọc env của mình; nạp env DEV cho lệnh tay: `python -c "import sys;sys.path.insert(0,'infra/scripts');import bootstrap_dev as b;b.load_into_environ()"` hoặc dùng `devenv.pg_dsn("identity")`.
jsonschema/pyyaml cần cho `tests/arch` và `tests/contract` (nếu thiếu: `pip install -r tests/arch/requirements.txt`).

## 6.2 Dùng `DevStack` (E2E và thử tay)

`infra/scripts/dev_stack.py`:
- `DevStack()` nạp `.env.dev`, cấp cổng ngẫu nhiên, dựng env cho từng service (issuer = URL Identity thật, feed key, JWKS URL, Redis nếu có).
- `reset_data()` = `DROP OWNED BY <svc>_svc CASCADE` trên từng DB (dựng lại **từ số 0**, kể cả dữ liệu seed của migration) → gọi `migrate_all()` ngay sau đó. (Đừng dùng TRUNCATE: sẽ xóa Platform/Metric Registry và catalog permission.)
- `start/stop/restart(name, **env)`, `overrides` (mô phỏng DB chết bằng DSN cổng 1, Redis chết…), `log_text(name)` đọc `.dev/logs/<name>.log`, `ready_status`.
- DEV Identity (`IDENTITY_ENV=dev`) in token xác thực email ra log — E2E lấy token từ đó (`tests/e2e/conftest.py::Api.verify_email`).

Khởi tạo user đầu tiên khi chưa có admin nào: seed một invite key thẳng vào `identity_db` (xem `Api.seed_invite_key`), đăng ký, xác thực email, đăng nhập. Không có lệnh "tạo admin" trong Identity (legacy có `legacy/admin-server/create_admin.py`).

## 6.3 Cách làm các thay đổi thường gặp

### Thêm module vào service
1. Sửa `modules` của service trong `infra/service-registry.json` (khai chức năng). 2. `python infra/scripts/scaffold_service.py` (idempotent) tạo `src/<package>/<module>/__init__.py` có docstring `module / service / phase / status / responsibility`.
3. Viết code + test trong module; đổi `status: planned` → `active` ở docstring khi xong. Test kiến trúc kiểm module trong registry khớp thư mục.

### Thêm bảng
Thêm vào `owned_tables` của **đúng** service → migration `NNNN_ten.sql` forward-only trong `services/<s>/migrations/` (test kiến trúc: migration chỉ được tạo bảng thuộc service; SQL không chạm bảng service khác).
**Không sửa migration đã áp dụng** (checksum lệch ⇒ migrate từ chối; DB DEV dựng lại bằng `reset_data()`). Cấp quyền chỉ cho user của service.

### Thêm event
Khai ở `emits` (service phát) và `consumes` (service nhận). Event có `id`, `type`, `data_version`; consumer **idempotent theo `id`**, có cursor bền, retry/backoff, dead-letter. Thêm trường vào `data` = tương thích; đổi/xóa trường = tăng `data_version`.
Ghi outbox **trong cùng transaction** với thay đổi nghiệp vụ và lấy advisory lock theo mẫu của `outbox`/`feed` hiện có (đừng tự cấp `seq` ngoài cơ chế này).

### Thêm endpoint
Request/response schema, `error.code` ổn định (thêm vào `manager_contracts.ErrorCode` hoặc `Extra` của service), scope/role, idempotency cho mutation, ví dụ, OpenAPI; cập nhật OpenAPI đóng băng **theo hướng additive** (Integration/Analytics có test chặn breaking); cập nhật `public_prefixes` + `route-map.json` nếu là route công khai mới, rồi `python infra/gateway/render_nginx.py` (test freshness).

### Thêm metric canonical
Thêm dòng vào Metric Registry (dữ liệu: `PUT /v1/integrations/admin/metrics/{key}`) và schema ingest nếu cần; **không thêm cột tùy tiện**; metric chưa vào registry nằm ở `extensions`.

### Thêm Tool mới (hoặc nền tảng mới)
**Không sửa Manager.** Admin thêm dòng Platform Registry (`PUT /v1/integrations/admin/platforms/{key}`); user đăng ký (kèm integration) hoặc admin tạo client; owner cấp credential; Tool dùng `manager_sdk.IntegrationClient` (`handshake → push_snapshot`). Tài liệu cho chủ Tool: `docs/api/integration-api-v1.md` + `ingest-data-contract-v1.md`.
Mẫu chạy thật: `tests/e2e/test_mock_tool_x.py`. Nếu bạn phải sửa code Manager chỉ để nhận tên Tool ⇒ ranh giới đang sai.

### Thêm website dùng đăng nhập chung
Đăng ký OIDC client (`POST /v1/admin/oidc/clients`), redirect URI chính xác; RP verify token bằng JWKS; xem `architecture-assessment-v2.md` §2.9.

### Thêm service mới
Registry (`status: scaffold`, cổng/DB/package) → `scaffold_service.py` → thêm DB + user vào `infra/docker/templates/initdb/01-databases.sql.tpl` (và Redis ACL nếu dùng) → `route-map.json` nếu có route legacy → `.env.example` → thêm `DevStack` nếu cần chạy trong E2E.

## 6.4 Quy ước mã

Python 3.12, FastAPI, type hints, `ruff` (E,F,W; 130 cột). Mã lỗi, tên field, đường dẫn API: **tiếng Anh**. Docstring/comment/tài liệu: **tiếng Việt** (khớp codebase). Không log mật khẩu/token/API key. Lỗi validation không phản chiếu input.
Commit nhỏ theo bước, thông điệp nêu phase + phạm vi. **Chỉ commit cục bộ.** "Xong" một phase = triển khai + test xanh + số liệu thật + tài liệu cập nhật + commit.

## 6.5 Quy tắc kiểm thử (đọc kỹ)

- Test chạy trên **Postgres/Redis thật** (DEV riêng) và **tiến trình thật** cho E2E; không mock DB. Test cần DB mà thiếu hạ tầng phải SKIP rõ lý do — **SKIP không phải PASS**.
- Mỗi thay đổi hành vi có test; mỗi quy tắc kiến trúc có "test của test" (ca giả lập vi phạm). Kiểm bằng **đột biến**: gỡ quy tắc → test phải đỏ → hoàn nguyên (các agent trước đã làm >130 ca; kịch bản nằm ngoài repo).
- **Không sửa/xóa test kiến trúc để cho qua**; muốn đổi quy tắc: đổi registry/ADR có chủ đích và giải thích ở commit. Không làm yếu assertion để xanh.
- Test của các service dùng chung DB DEV (`identity_db`, `integration_db`…): **không chạy song song** nhiều bộ test cùng service (có thể làm nhau đỏ ngẫu nhiên). `test_all.py` chạy tuần tự.
- Cặp `tests/contract` và `tests/integration` nằm ở hai thư mục có `pytest.ini` khác nhau — chạy riêng từng thư mục (chạy chung một lệnh gây `RuntimeError` ở fixture).

## 6.6 Bẫy thường gặp (đã tốn thời gian)

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `Migration lỗi: 000X đã áp dụng nhưng nội dung bị sửa (checksum lệch)` | Ai đó sửa migration đã chạy. Đừng sửa; DEV: `DevStack.reset_data()` rồi migrate |
| Identity trả 500 `InvalidToken` khi login | `signing_keys` trong DB mã hóa bằng Fernet khác (test cách ly dùng khóa ngẫu nhiên). Dựng lại DB DEV |
| Analytics không thấy dữ liệu mới | Kiểm `ANALYTICS_INTEGRATION_URL/FEED_KEY` khớp `INTEGRATION_FEED_KEY_ANALYTICS`; consumer chạy ~5 s/vòng; xem `/v1/analytics/freshness` và `admin/dead-letters` |
| Provisioning client không tạo / `PLATFORM_NOT_ALLOWED` trong log Integration | Platform chưa nằm trong Platform Registry **trước** khi user đăng ký ⇒ event vào dead-letter. Admin thêm platform rồi replay (`python -m integration_service.provisioning replay`) |
| `METRIC_UNKNOWN` trong `rejections` | Metric chưa có trong Metric Registry ⇒ thêm bằng `PUT /admin/metrics/{key}` hoặc đặt vào `extensions`. (Nếu DB vừa bị TRUNCATE, registry seed cũng mất) |
| Credential không cấp được / `EMAIL_NOT_VERIFIED` | Owner chưa verify email, hoặc `account_projection` chưa nhận event `account.verified` (consumer Integration chạy ~5 s/vòng) |
| Email đăng ký test bị từ chối "special-use or reserved name" | `email-validator` chặn `.test`, `.local`… Dùng `…@e2e.example.com` |
| Windows: console cp1252 làm vỡ tiếng Việt / test `report_distribution` lỗi | Đặt `PYTHONIOENCODING=utf-8` |
| Git Bash cắt cụt heredoc dài hoặc chứa ký tự lạ | Ghi file bằng công cụ ghi file / script Python từ file; tránh NUL (`\x00`) thô trong nguồn |
| Dòng `warning: LF will be replaced by CRLF` | Vô hại (Windows `core.autocrlf`); `.gitattributes` ghim LF cho `*.acl`, `*.sql`, `Dockerfile` (Redis ACL/ SQL cần LF) |
| `redis/users.acl` làm Redis không khởi động | File ACL **không được có dòng chú thích `#`** |
| Compose đè container của project khác | Không bao giờ bỏ `name:` / tên tường minh `mp-dev-*`; test guard chặn |
| uvicorn tay làm rò token trong log | Chạy gateway với `--no-proxy-headers --no-server-header --no-access-log` |
| `ImportError manager_sdk` ở reward khi deploy | SDK không phải gói PyPI: `REWARD_SDK_PATH` hoặc `vendor/` |
| `render_nginx` freshness test đỏ | Đổi `public_prefixes`/route-map ⇒ chạy lại `python infra/gateway/render_nginx.py` |
| Test đỏ ngẫu nhiên ở identity khi chạy cùng lúc nhiều thứ | Chung `identity_db` DEV ⇒ chạy tuần tự |

## 6.7 Môi trường làm việc với Agent

- Ranh giới nghiêm: **không** `git push`, không deploy, không chạm VPS/CI/CD, không chạm container/volume/cổng ngoài `mp-dev-*` (đặc biệt Redis/RAGFlow của project khác trên cùng máy).
- Khi chia việc song song cho nhiều agent: mỗi agent sở hữu **thư mục riêng**; file dùng chung (`CLAUDE.md`, `packages/`, `tests/arch`, `infra/scripts`, `infra/service-registry.json` ngoài mục của mình) do điều phối viên sửa; agent không commit — điều phối viên chạy lại test của agent + `tests/arch` rồi mới commit.
- Báo cáo trung thực: test đỏ thì nói đỏ; bước chưa chạy được thì nói chưa chạy; không báo xong khi còn blocker giải được trong DEV.
