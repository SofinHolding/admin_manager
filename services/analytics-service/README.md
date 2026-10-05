# analytics-service

Toàn bộ thống kê và Dashboard: KPI, traffic, nền tảng, cá nhân, quốc gia, kênh, nhóm, freshness.

| | |
|---|---|
| Cổng | 8432 |
| Database (riêng) | `analytics_db` |
| Package | `analytics_service` |
| Trạng thái | scaffold (chưa có nghiệp vụ; module được hiện thực theo phase) |

## Module

| Module | Phase | Trách nhiệm |
|---|---|---|
| `filters` | 3 | FilterContext duy nhất cho mọi KPI và biểu đồ |
| `feed_consumer` | 3 | kéo change feed, idempotent theo event id, replay từ cursor |
| `readmodels` | 3 | dựng và cập nhật read model từ dữ liệu canonical |
| `kpi` | 3 | summary: tổng lượt xem, video đăng, kênh quản lý/hoạt động |
| `traffic` | 3 | chuỗi traffic theo chỉ số |
| `breakdown` | 3 | theo nền tảng, cá nhân, quốc gia, kênh/page, nhóm kênh |
| `country_comparison` | 3 | so sánh quốc gia (biểu đồ riêng) |
| `groups` | 3 | nhóm kênh và nhãn nền tảng do viewer quản lý |
| `country_config` | 3 | alias và tên chuẩn quốc gia |
| `freshness` | 3 | as_of, lag, cảnh báo dữ liệu cũ |
| `cache` | 3 | Cache-Aside với Redis, invalidate sau commit |

## Dữ liệu và sự kiện (theo `infra/service-registry.json`)

- Bảng sở hữu: `read_resources`, `read_content`, `read_daily_metrics`, `read_events`, `agg_daily`, `channel_groups`, `channel_group_members`, `country_aliases`, `country_canonicals`, `feed_cursor`, `processed_events`, `account_projection`
- Phát event: —
- Nhận event: `snapshot.committed`, `resource.changed`, `account.status_changed`, `account.deleted`

## Chạy

```bash
cd services/analytics-service
python -m pytest -q                  # test riêng của service
python -m uvicorn analytics_service.main:app --port 8432
```

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
