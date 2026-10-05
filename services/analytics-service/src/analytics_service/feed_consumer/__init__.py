"""module: feed_consumer
service: analytics-service
phase: 3
status: active
responsibility: kéo change feed, idempotent theo event id, replay từ cursor
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

Vòng lặp: đọc feed Integration -> với `snapshot.committed`/`resource.changed` kéo `canonical/*` từ `after_revision` đã lưu ->
upsert read model -> ghi `processed_events` + `feed_cursor` CÙNG transaction -> tăng seal cache. Cuối mỗi vòng LUÔN so
`canonical/summary.head_revision` với revision đã áp dụng nên mất event cũng không sao. Event lỗi: retry backoff có giới hạn
-> `dead_letters` và đi tiếp (có lệnh replay). Nguồn chết: giữ nguyên con trỏ, ghi trạng thái nguồn, không crash.
Feed Identity nuôi `account_projection`.
"""
