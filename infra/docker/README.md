# Hạ tầng dữ liệu dev (Postgres + Redis) — tách biệt hoàn toàn khỏi các stack khác trên máy

Dùng riêng cho dự án này. **Không đụng** container, volume, network hay cổng của project khác.

| Thành phần | Tên | Cổng host |
|---|---|---|
| Compose project | `manager-platform-dev` | |
| Postgres 16 | container `mp-dev-postgres`, volume `mp_dev_pgdata` | `127.0.0.1:55440` |
| Redis 7 | container `mp-dev-redis`, volume `mp_dev_redisdata` | `127.0.0.1:56390` |
| Network | `mp_dev_net` | |

```bash
docker compose -f infra/docker/compose.dev.yml up -d      # tạo
docker compose -f infra/docker/compose.dev.yml ps         # xem
docker compose -f infra/docker/compose.dev.yml down       # dừng (giữ dữ liệu)
docker compose -f infra/docker/compose.dev.yml down -v    # dừng và XÓA dữ liệu của stack này
```

`down -v` chỉ xóa volume `mp_dev_*` của stack này vì tên được đặt tường minh.

## Cô lập dữ liệu

- Postgres: 4 database (`identity_db`, `integration_db`, `analytics_db`, `reward_db`), mỗi database một user riêng
  (`identity_svc`...), `REVOKE ALL FROM PUBLIC`. User của service này không kết nối được database của service khác.
  Script khởi tạo `initdb/01-databases.sql` chỉ chạy khi volume còn trống.
- Redis: user `default` bị tắt; mỗi service một user chỉ truy cập key có prefix của mình (`analytics:*`),
  không có lệnh nguy hiểm (`FLUSHALL`, `KEYS`...). Hiện chỉ `analytics_cache` (quyết định D14).
- File `redis/users.acl` **không được chứa dòng chú thích `#`** (Redis từ chối khởi động). Chú thích để ở đây.

## Kiểm chứng

```bash
TEST_PG_HOST=127.0.0.1 TEST_PG_PORT=55440 TEST_REDIS_HOST=127.0.0.1 TEST_REDIS_PORT=56390 \
  python -m pytest tests/arch/test_infra.py -rs
```

Chỉ dùng mật khẩu dev trong repo này. Production: secret manager.
