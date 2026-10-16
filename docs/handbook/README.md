# Manager Platform — Handbook kỹ thuật

Bộ tài liệu cho **lập trình viên hoặc Agent mới** vào tiếp quản dự án. Đọc theo thứ tự dưới đây là đủ để làm việc mà không cần hỏi lại người viết.
Mọi nội dung phản ánh trạng thái repo tại **2026-10-06**, nhánh `refactor/microservices`. Khi mã và tài liệu mâu thuẫn, **mã + test thắng**; hãy sửa tài liệu.

> Tài liệu RÀNG BUỘC (luật không được phá) là [`/CLAUDE.md`](../../CLAUDE.md). Handbook này MÔ TẢ; CLAUDE.md QUY ĐỊNH.

## Thứ tự đọc

| # | Tài liệu | Trả lời câu hỏi |
|---|---|---|
| 1 | [01-tong-quan-he-thong.md](01-tong-quan-he-thong.md) | Hệ thống là gì, gồm những gì, vì sao thiết kế thế này? Cấu trúc repo, công nghệ |
| 2 | [02-trang-thai-tinh-nang.md](02-trang-thai-tinh-nang.md) | **Đã làm gì / chưa làm gì / làm dở gì**, theo phase và theo service |
| 3 | [03-dich-vu.md](03-dich-vu.md) | Từng service: module, bảng, endpoint, event, biến môi trường, điểm tinh tế bên trong |
| 4 | [04-du-lieu-va-su-kien.md](04-du-lieu-va-su-kien.md) | Dữ liệu chảy thế nào: ingest → canonical → feed → Analytics → Dashboard; cache; hợp đồng |
| 5 | [05-xac-thuc-va-bao-mat.md](05-xac-thuc-va-bao-mat.md) | Ba mặt phẳng xác thực, token, thu hồi, khóa, kiểm soát bảo mật |
| 6 | [06-huong-dan-phat-trien.md](06-huong-dan-phat-trien.md) | Dựng môi trường, chạy, test, thêm module/bảng/event/endpoint/Tool, bẫy thường gặp |
| 7 | [07-van-hanh-va-lo-trinh.md](07-van-hanh-va-lo-trinh.md) | Việc production chờ duyệt, backlog ưu tiên cho người tiếp theo, giới hạn đã biết |

## Tài liệu gốc (nguồn sự thật cho từng chủ đề)

| Chủ đề | File |
|---|---|
| Luật ràng buộc, quy trình thay đổi | [`/CLAUDE.md`](../../CLAUDE.md) |
| Kiến trúc đích, quyết định D1–D14, bounded context, ma trận sở hữu | [`architecture/architecture-assessment-v2.md`](../architecture/architecture-assessment-v2.md) |
| Quyết định phát sinh khi hiện thực (ADR-001…011) | [`architecture/implementation-decisions.md`](../architecture/implementation-decisions.md) |
| Quy ước chassis service | [`architecture/service-conventions-v1.md`](../architecture/service-conventions-v1.md) |
| Chuẩn tổ chức thư mục + kế hoạch di chuyển (chờ duyệt) | [`architecture/repo-layout-v1.md`](../architecture/repo-layout-v1.md) |
| Sơ đồ tương tác (HTML) | [`architecture/manager-platform-architecture.html`](../architecture/manager-platform-architecture.html), `cache-aside-*-sequence.html`, `rp-login-revocation-sequence.html` |
| Xác thực giữa service | [`api/service-auth-contract-v1.md`](../api/service-auth-contract-v1.md) |
| Feed nội bộ + canonical export | [`api/internal-feed-contract-v1.md`](../api/internal-feed-contract-v1.md) |
| Hợp đồng dữ liệu Tool đẩy lên | [`api/ingest-data-contract-v1.md`](../api/ingest-data-contract-v1.md), schema `packages/contracts/ingest/2026-10-01/` |
| Thay đổi API dành cho Tool đã liên kết (mã nền tảng, định dạng payload) | [`api/integration-tool-changes-2026-10-16.md`](../api/integration-tool-changes-2026-10-16.md) |
| API Integration / Analytics | [`api/integration-api-v1.md`](../api/integration-api-v1.md), [`api/analytics-api-v1.md`](../api/analytics-api-v1.md); OpenAPI đóng băng `docs/api/openapi/*.json` |
| API legacy (baseline) | `docs/api/legacy/*.openapi.json` |
| Báo cáo triển khai + số liệu test | [`operations/implementation-final-report.md`](../operations/implementation-final-report.md) |
| Chuyển production (mọi bước `PENDING_PRODUCTION_APPROVAL`) | [`operations/production-cutover-runbook.md`](../operations/production-cutover-runbook.md), [`operations/identity-migration-runbook.md`](../operations/identity-migration-runbook.md) |
| Hạ tầng DEV | [`infra/docker/README.md`](../../infra/docker/README.md) |

## Bắt đầu nhanh (10 phút)

```bash
python infra/scripts/bootstrap_dev.py                      # sinh .env.dev (secret ngẫu nhiên, không commit)
docker compose --env-file .env.dev -f infra/docker/compose.dev.yml up -d   # Postgres 55440 + Redis 26390 RIÊNG của dự án
python infra/scripts/test_all.py --fast                    # kiểm tra nhanh (bỏ tiến trình thật/e2e/frontend)
python infra/scripts/dev_stack.py up                       # chạy cả 5 service bằng tiến trình thật (Ctrl+C để dừng)
```

Ba điều tuyệt đối nhớ:
1. **Chỉ commit cục bộ, KHÔNG `git push`, KHÔNG deploy, KHÔNG đụng VPS/CI/CD** khi chưa có chỉ thị rõ ràng của chủ dự án.
2. **Chỉ dùng Postgres/Redis DEV của dự án** (`mp-dev-*`). Máy dev còn chạy stack khác (vd RAGFlow) — không bao giờ đụng tới container/volume/cổng của chúng.
3. Muốn thêm Tool mới → **không sửa Manager** (cấp credential + đưa tài liệu API). Nếu thấy phải sửa Manager chỉ để nhận tên Tool thì ranh giới đang sai.
