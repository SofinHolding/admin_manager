"""`mapping_status` của kênh = "đã liên kết số liệu": kênh ĐÃ CÓ số liệu trên hệ thống (video hoặc chỉ số theo ngày) thì là `mapped`.

Trước đây trường này chỉ là cờ lưu sẵn trong `data.extensions.legacy.mapping_status` (mặc định `unmapped`) và KHÔNG có đường nào đổi nó
khi Tool/collector đẩy số liệu, nên kênh đã có dữ liệu vẫn hiện "Chưa liên kết số liệu". Giá trị hiệu lực giờ suy ra lúc đọc từ chính dữ liệu
(không ghi thêm gì, không phát sự kiện, không đổi revision); giá trị lưu sẵn chỉ còn là mặc định khi chưa có số liệu.

Các biểu thức dưới đây giả định bảng `resources` có bí danh `r`.
"""
from __future__ import annotations

HAS_DATA_SQL = (
    "(EXISTS (SELECT 1 FROM content_items ci_m WHERE ci_m.resource_id = r.id AND NOT ci_m.removed) "
    "OR EXISTS (SELECT 1 FROM daily_metrics dm_m WHERE dm_m.resource_id = r.id AND NOT dm_m.removed))")

EFFECTIVE_MAPPING_SQL = (
    f"(CASE WHEN {HAS_DATA_SQL} THEN 'mapped' "
    "ELSE COALESCE(r.data -> 'extensions' -> 'legacy' ->> 'mapping_status', 'unmapped') END)")
