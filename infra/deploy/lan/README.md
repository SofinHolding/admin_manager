# Triển khai lên máy chủ LAN (Docker Compose)

Dùng đúng stack của repo (`infra/compose/stack.yml`) cộng một nginx phục vụ web. Chạy ở chế độ `dev` vì identity ở `production`
bắt buộc issuer `https://`; khi có TLS/domain thì đổi `*_ENV` sang `production` (xem `docs/operations/production-cutover-runbook.md`).
Mọi tên container/network/volume có tiền tố `mp-` / `manager-platform-*` nên không đụng project khác trên cùng máy.

## Các bước (đã chạy trên 192.168.1.70)

1. Đưa mã lên: `git archive --format=tar.gz HEAD` rồi giải nén vào `~/manager-platform`; build web `npm run build` ở `apps/manager-web`
   và chép `dist/` sang `apps/manager-web/dist` (thư mục `dist` không nằm trong git).
2. Sinh secret trên máy chủ: `python3 infra/scripts/bootstrap_dev.py` (tạo `.env.dev` 600 và `.dev/`, KHÔNG commit).
   Khi chuyển từ server cũ: đặt `REWARD_TOKEN_KEY` bằng khóa cũ qua `bootstrap_dev.write_env`, nếu không token Discord đã mã hóa không giải mã được.
3. Dữ liệu: `docker compose --env-file .env.dev -f infra/docker/compose.dev.yml up -d`
4. Nền tảng; địa chỉ công khai và CIDR proxy nằm trong `infra/deploy/lan/lan.env` (sửa file đó khi đổi domain):
   `docker compose --env-file .env.dev --env-file infra/deploy/lan/lan.env -f infra/compose/stack.yml up -d --build`
5. Web: `docker compose -f infra/deploy/lan/web.yml up -d` (cổng `MP_WEB_PORT`, mặc định 8480; `/v1/*` chuyển sang gateway, `/internal/*` bị chặn).
6. Domain `statsdash.dnlabs.space`: cần MỘT tunnel Cloudflare có public hostname trỏ tới `http://mp-web:80` (hoặc `http://192.168.1.70:8480`).
   Hoặc thêm hostname vào tunnel `mail` đang chạy trên máy, hoặc tạo tunnel riêng rồi chạy `infra/deploy/lan/tunnel.yml`
   (token trong `.dev/cloudflared.env`, không commit). KHÔNG dùng token của tunnel đã phục vụ dự án khác: connector thứ hai của cùng
   tunnel sẽ nhận một phần lưu lượng của dự án đó. Trước khi có route, LAN vẫn dùng `http://192.168.1.70:8480`.

## Nhập người dùng từ hệ cũ

Từ dump CHỈ gồm `accounts`, `invite_keys`, `refresh_tokens` (+ `reward_discord_credentials`, `reward_discord_tokens`):
restore vào một DB tạm, rồi chạy trong container identity
`python -m identity_service.legacy_sync --source postgresql://…/<db tạm>` và `--parity` (phải `ok: true`).
Công cụ giữ nguyên id, hash mật khẩu, phiên; phát `account.registered`/`account.verified`; cấp capability mặc định theo role.
Quyền đặc biệt (vd `capability:tool_integration` để có khóa Tool cá nhân) KHÔNG tự cấp: admin cấp sau.
Hai bảng reward nạp bằng `pg_restore --data-only -t reward_discord_credentials -t reward_discord_tokens -d reward_db`.
