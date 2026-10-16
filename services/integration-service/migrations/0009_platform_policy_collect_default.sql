-- integration_db 0009: kênh thêm tay trên MỌI nền tảng đều được giao cho Tool dùng chung (collector) kéo số liệu.
-- Trước đây chỉ facebook/tiktok/youtube bật `fallback_enabled`; instagram bị seed `false` nên kênh Instagram nhập tay mang chế độ
-- "disabled" (không thu thập). Collector tự chỉ nhận platform nó có adapter nên job của platform chưa hỗ trợ chỉ nằm chờ.
-- Chỉ đổi dòng seed CHƯA từng được admin chỉnh (updated_by rỗng). Idempotent, forward-only.
UPDATE legacy_ingest.platform_policies
   SET fallback_enabled = true
 WHERE platform = 'instagram' AND fallback_enabled = false AND updated_by = '';
