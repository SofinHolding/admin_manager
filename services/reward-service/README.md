# reward-service

Phân phối điểm thưởng qua Discord (engine job slash-command, SSE). Cổng `8422`, DB riêng `reward_db`.
Xác thực: token RS256 của Identity (JWKS, verify offline); quyền `reward:access` / `reward:admin` do Identity quản lý; thu hồi qua feed Identity.

```text
src/reward_service/   api/ domain/ engine/ discord/ identity_link/ store/ + main.py settings.py security.py crypto.py
migrations/           001…004 (forward-only; REWARD_DB_MIGRATE_ON_BOOT=1 áp khi khởi động)
tests/                pytest (cần Postgres DEV: devenv.pg_dsn("reward"))
run.sh, DEPLOY.md     chạy bằng pm2 trên VPS (legacy), kế hoạch chuyển DB
```

- Chạy: `cd services/reward-service && PYTHONPATH=src python -m uvicorn reward_service.main:app --port 8422 --workers 1` (**luôn `--workers 1`**).
- Test: `cd services/reward-service && python -m pytest -q` · migrate tay: `PYTHONPATH=src python -m reward_service.store.migrate`.
- Cấu hình: `.env.example` (tiền tố `REWARD_`); `REWARD_DATABASE_URL` bắt buộc, không có fallback.
- Mô tả đầy đủ: `docs/handbook/03-dich-vu.md` §3.5.
