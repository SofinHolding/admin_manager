# infra/compose — chạy cả nền tảng bằng container (DEV)

`stack.yml` build 5 service từ Dockerfile của từng service (context = gốc repo), có job migration riêng cho identity/integration/analytics (reward tự migrate khi khởi động), chỉ bind `127.0.0.1`.

```bash
python infra/scripts/bootstrap_dev.py
docker compose --env-file .env.dev -f infra/docker/compose.dev.yml up -d              # Postgres + Redis DEV riêng (network mp_dev_net)
docker compose --env-file .env.dev -f infra/compose/stack.yml up -d --build           # 5 service + migration
docker compose --env-file .env.dev -f infra/compose/stack.yml up -d --build identity  # build/restart RIÊNG một service
docker compose --env-file .env.dev -f infra/compose/stack.yml down                    # dừng (không xóa dữ liệu)
```

Cổng: gateway `8400`, identity `8430`, integration `8431`, analytics `8432`, reward `8422`. Đã kiểm chứng: cả 5 image build được và `/health/ready` = 200 (gateway chuyển được `/.well-known/jwks.json`).
**Không dùng cho production.** Thêm service mới: thêm một khối `<tên>-migrate` (nếu có DB) + `<tên>` theo mẫu có sẵn; không sửa khối của service khác.
