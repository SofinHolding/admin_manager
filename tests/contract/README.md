# Contract tests

Bảo vệ ranh giới giữa các hệ thống bằng hợp đồng, không bằng niềm tin.

| Loại | Trạng thái |
|---|---|
| Hợp đồng ingest (schema + ví dụ + ca sai bị từ chối) | `test_ingest_contract.py` — đang chạy |
| Characterization của contract `legacy` (shape response cũ) | Phase 1.2: viết khi extract Identity |
| OpenAPI diff chặn breaking change của từng service | Phase 1.2 trở đi, cùng với OpenAPI thật của service |
| Consumer-driven (SDK web, SDK Tool) | Phase 2 |

Quy tắc: thay đổi **breaking** (xóa/đổi nghĩa trường hoặc mã lỗi) bắt buộc tạo thư mục contract version mới
(`packages/contracts/<tên>/<ngày>/`), không sửa version đang có.
