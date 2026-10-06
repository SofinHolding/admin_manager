# Implementation Decisions (ADR log)

Ghi mọi quyết định kiến trúc/bảo mật phát sinh khi triển khai. Thứ tự thời gian. Quyết định đã được người dùng duyệt (D1-D14) nằm ở
`architecture-assessment-v2.md` mục 8; file này là phần BỔ SUNG và phần đã được **làm rõ khi hiện thực**.

Quy ước: **ID** · Quyết định · Lý do · Hệ quả/đánh đổi.

## Nền tảng & bảo mật DEV

**ADR-001 · Secret DEV sinh ngẫu nhiên, không nằm trong repo.**
`infra/scripts/bootstrap_dev.py` sinh `.env.dev` (gitignored) và dựng initdb/ACL từ template vào `.dev/` (gitignored). Compose đọc secret
từ env. Không còn mật khẩu mặc định (`dev_*_change_me`) hay JWT secret mặc định (chuỗi `dev-secret-...` cũ của admin-server) ở bất kỳ file nào đã commit;
test quét secret (`tests/arch/test_infra.py`) giữ điều đó.
Lý do: yêu cầu bảo mật DEV của tài liệu. Hệ quả: mỗi máy dev phải chạy bootstrap một lần; `--rotate` sinh lại và cần dựng lại volume.

**ADR-002 · admin-server legacy không còn JWT secret mặc định.**
`auth.py` thiếu `JWT_SECRET` ⇒ `MODE=dev` tự sinh secret ngẫu nhiên theo tiến trình, còn lại từ chối khởi động; giá trị `dev-*` bị chặn ngoài dev.
Lý do: lỗ hổng thật trong code đang chạy production. Hệ quả: **thay đổi hành vi khi triển khai production** ⇒ `PENDING_PRODUCTION_APPROVAL`
(deploy legacy phải có `JWT_SECRET` thật — vốn đã bắt buộc trong thực tế).

**ADR-003 · Stack dữ liệu DEV tách biệt hoàn toàn.**
Compose project `manager-platform-dev`, container/volume/network đặt tên tường minh `mp-dev-*`/`mp_dev_*`, cổng 55440/56390 chỉ bind `127.0.0.1`.
Lý do: sự cố ghi đè container `redis` của project khác trên máy dev (project mặc định `docker` trùng tên). Hệ quả: test guard chặn tên chung chung.

**ADR-004 · Cô lập DB bằng 4 database + 4 user không đặc quyền; Redis ACL theo prefix.**
`REVOKE ALL ... FROM PUBLIC`, `GRANT CONNECT` chỉ cho chủ; user runtime không superuser. Test 4×4 kết nối chéo bị từ chối. Redis `default off`, user theo service
chỉ chạm `analytics:*`, `-@dangerous`.

## Xác thực

**ADR-005 · Access token RS256 + JWKS, khoá private mã hoá Fernet trong DB.**
Khoá giải mã (`IDENTITY_KEY_ENCRYPTION_KEY`) nằm ngoài DB (env). Dump DB không đủ để ký token. Đúng một khoá `active` (chỉ mục duy nhất một phần),
khoá `retired` vẫn publish trong thời gian ân hạn. Xoay bằng `python -m identity_service.keys rotate`.
Chế độ HS256 chỉ tồn tại để cùng sống với legacy/admin-server/reward legacy (`IDENTITY_ACCESS_TOKEN_ALG=HS256`); mặc định RS256 và KHÔNG có secret chung.

**ADR-006 · Phiên (`sid`) và thu hồi.**
Mỗi lần đăng nhập tạo `sessions`; access token mang `sid`. Đăng xuất / khoá tài khoản / đổi mật khẩu / đổi role thu hồi phiên NGAY tại Identity
(introspection và mọi endpoint của Identity từ chối). Service khác verify OFFLINE nên độ trễ thu hồi tại đó bị chặn bởi TTL access token (cấu hình
`IDENTITY_ACCESS_TOKEN_TTL_S`) cộng back-channel logout/introspection cho Relying Party (mục 2.9 assessment). Đây là đánh đổi có chủ đích: không có cạnh đồng bộ
Identity→mọi service trên đường request.

**ADR-007 · Permission thuộc Identity (D7).**
`permissions` (catalog, `admin_only`) + `account_permissions`; claim `perms`. `role=admin` coi như có mọi quyền quản trị; `dash_admin` không cấp quyền `admin_only`.
Shim: role legacy `discord` ⇒ mặc định có `reward:access` (bỏ ở Phase 6).

## Bất đồng bộ

**ADR-008 · Outbox + feed kéo là đường bền; Redis chỉ tăng tốc (D2).**
`seq` theo thứ tự commit nhờ `pg_advisory_xact_lock` lấy trước `nextval`. Test chứng minh: gỡ khoá ⇒ consumer mất sự kiện; có khoá ⇒ đủ.
Đánh đổi: các transaction ghi outbox bị tuần tự hoá (chấp nhận ở quy mô này).

**ADR-009 · Analytics kéo trạng thái canonical theo `revision`, không dựa hoàn toàn vào payload event.**
Event `snapshot.committed` chỉ là tín hiệu; dữ liệu lấy từ `GET /internal/v1/canonical/{kind}?after_revision=`. Mất event vẫn đồng bộ lại; backfill = revision 0.

**ADR-010 · Xác thực nội bộ bằng `X-Service-Key` theo cặp (nguồn→bên đọc).**
Đơn giản, thu hồi riêng, không cần phát hành token máy cho feed. Gateway không bao giờ route `/internal/*`. Xoay khoá = đổi env + khởi động lại hai đầu
(ghi chú vận hành; `PENDING_PRODUCTION_APPROVAL` khi áp dụng thật).

## Chuỗi cung ứng

**ADR-011 · Kiểm lỗ hổng dependency Python (pip-audit, chạy được vì có mạng).**
Kết quả lần chạy đầu: `pyjwt 2.14.0` (PYSEC-2026-4141, sửa ở 2.15.0) — lỗi nằm ở `PyJWKClient.get_signing_key_from_jwt`, **không dùng** trong dự án
(verify bằng JWKS tự cài của `manager_sdk`); vẫn nâng ràng buộc lên `>=2.15.0`. `pytest 8.4.2` (PYSEC-2026-1845): chỉ ảnh hưởng thư mục `/tmp` trên UNIX, là dependency
chỉ dùng cho test, máy dev là Windows ⇒ ghi ngoại lệ có thời hạn, không nâng major hàng loạt.

## Cấu trúc thư mục

**ADR-012 · Repo Layout v1 (`docs/architecture/repo-layout-v1.md`).**
Chủ dự án yêu cầu dựng lại cấu trúc cho rõ ràng, không cần giữ nguyên cũ. Quyết định: `apps/` (manager-web, local-fallback-tool), `services/` (5 backend cùng skeleton, gồm reward-service), `legacy/` (admin-server đóng băng),
`packages/`, `infra/{gateway,docker,compose,deploy,scripts}`, `tests/`, `docs/`; `tasks/` và `.omp/` giữ nguyên (bị tham chiếu bằng đường dẫn). Di chuyển bằng lệnh hệ điều hành (mang theo file ignore như `node_modules`, `admin-auth.db`); đổi tên thư mục
nhưng **không** đổi tên package npm/Python hay khóa registry, và **không** sửa migration đã áp dụng (checksum). reward-service được chuẩn hóa thành `src/reward_service/` + `migrations/` ở gốc service (180 test là lưới an toàn).
Workflow deploy (chỉ `workflow_dispatch`) được sửa đường dẫn trong repo; đường dẫn trên VPS (`/srv/admin`, `/srv/reward`) không đổi. Hệ quả: mọi tham chiếu đường dẫn bị cứng đã được cập nhật; `tests/arch/test_repo_layout.py` giữ chuẩn (gốc repo, skeleton service/app, legacy đóng băng, registry, compose).
