"""module: freshness
service: analytics-service
phase: 3
status: active
responsibility: as_of, lag, cảnh báo dữ liệu cũ
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).

`freshness` = `{as_of, committed_at, snapshot_id, revision, lag_seconds, stale}` đi kèm MỌI response dữ liệu.
`stale=true` khi lag > ANALYTICS_STALE_AFTER_S hoặc nguồn feed không đọc được; dashboard vẫn trả dữ liệu cũ, không 500.
"""

from analytics_service.freshness.service import SyncInfo, freshness_block, load_sync_info

__all__ = ["SyncInfo", "freshness_block", "load_sync_info"]
