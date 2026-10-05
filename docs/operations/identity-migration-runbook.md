# Runbook: chuyển Identity từ admin-server sang identity-service

> **Chưa thực thi.** Tài liệu này mô tả quy trình cho Phase 1.2 (shadow) và 1.3 (cutover). Mọi bước chạm VPS hay DB
> production **chỉ được làm sau khi người dùng chấp thuận rõ ràng**. Hiện chỉ đã kiểm chứng trên hạ tầng dev `mp-dev-*`.

## 0. Điều kiện tiên quyết

| Điều kiện | Cách kiểm |
|---|---|
| Postgres có `identity_db` và user `identity_svc` (quyền chỉ trên DB này) | `infra/docker/initdb/01-databases.sql` (dev); production dùng secret manager |
| Một user **chỉ đọc** trên DB legacy (`ufsync`) cho 3 bảng `invite_keys`, `accounts`, `refresh_tokens` | `GRANT SELECT` riêng, không dùng user của admin-server |
| Migration `identity_db` đã áp dụng | `python -m identity_service.migrate` |
| Test xanh | `python infra/scripts/test_all.py` (có `TEST_PG_HOST`) |

## 1. Shadow: đồng bộ một chiều và đối chiếu (Phase 1.2) — an toàn, lặp lại được

Legacy vẫn là nơi ghi duy nhất. identity-service chưa nhận lưu lượng thật.

```bash
export IDENTITY_DATABASE_URL=postgresql://identity_svc:***@host:5432/identity_db
# 1) chạy thử: ghi trong transaction rồi rollback, in số liệu
python -m identity_service.legacy_sync --source postgresql://legacy_reader:***@host:5432/ufsync --dry-run
# 2) đồng bộ thật
python -m identity_service.legacy_sync --source postgresql://legacy_reader:***@host:5432/ufsync
# 3) đối chiếu; thoát 0 khi khớp hoàn toàn
python -m identity_service.legacy_sync --source postgresql://legacy_reader:***@host:5432/ufsync --parity
```

Bảo đảm của công cụ (đều có test):
- Chỉ đọc nguồn (transaction read-only). Chạy lại bao nhiêu lần cũng được: chỉ cập nhật phần khác biệt.
- Giữ nguyên `id`, bcrypt hash, `used_count`. Token legacy lưu rõ được **băm** khi nhập; link xác thực/đặt lại mật
  khẩu đang chờ và phiên đang đăng nhập (refresh token) vẫn dùng được.
- Dòng sai (role lạ, `used_count > max_uses`, xung đột username/email) được **báo và bỏ qua**, không làm hỏng lượt chạy.
- Từ chối chạy khi identity đã phục vụ lưu lượng thật (có `auth_audit`), trừ khi `--force`.

Lịch đề xuất: chạy định kỳ (ví dụ mỗi 5 phút) trong cửa sổ shadow để `--parity` luôn gần 0 độ trễ.

**Tiêu chí đi tiếp (go/no-go):** `--parity` trả `ok=true` liên tục ≥ 24 giờ; báo cáo `skipped` đã được xem xét
từng dòng; diễn tập cutover và rollback trên staging thành công.

## 2. Cutover single-writer (Phase 1.3) — CẦN DUYỆT TRƯỚC KHI LÀM

Nguyên tắc: tại mọi thời điểm chỉ có **một** writer cho `accounts`, `invite_keys`, `refresh_tokens`.

| Bước | Hành động | Kiểm chứng |
|---|---|---|
| 1 | Freeze: các endpoint ghi của auth trả `503 + Retry-After` vài giây | log gateway |
| 2 | Chạy `legacy_sync` lần cuối rồi `--parity` | `ok=true` |
| 3 | Đổi route gateway `/v1/auth/*`, `/v1/admin/keys|users` sang identity-service; bật cờ proxy ở admin-server | `curl` thử đăng nhập |
| 4 | `REVOKE INSERT, UPDATE, DELETE` trên các bảng auth của legacy cho user của admin-server | admin-server không còn ghi được |
| 5 | Theo dõi: tỉ lệ 4xx/5xx, latency đăng nhập, `auth_audit` tăng | dashboard |
| Rollback | Lật route về legacy. Ghi mới trên identity_db nằm trong `auth_audit`/bảng của nó để replay ngược | diễn tập trước |

Sau bước 3, **không chạy `legacy_sync` nữa** (công cụ tự từ chối vì `auth_audit` không rỗng).

## 3. Token trong giai đoạn chuyển tiếp

identity-service hiện cấp JWT HS256 dùng **cùng secret** với admin-server/reward-service (đúng cửa sổ chuyển tiếp).
Token hai bên dùng chéo được (test `test_tokens_issued_by_each_side_are_accepted_by_the_other`). Phase 1.4 thay bằng
RS256 + JWKS, chấp nhận cả hai trong tối đa 15 phút (TTL access token), sau đó bỏ secret chung.

## 4. Khác biệt có chủ đích so với legacy (đã kiểm thử)

| Khác biệt | Lý do |
|---|---|
| Mã đổi mật khẩu 6 số tách khỏi token reset; tối đa 5 lần thử | legacy dò được 10^6 mã qua `/reset-password` (không đăng nhập, không rate limit) |
| Link xác thực email không mở khoá tài khoản `suspended` | legacy gỡ khoá của admin |
| Key hết hạn không đăng ký được | legacy chỉ kiểm hạn ở `validate-key` |
| Dùng key + tạo account trong một transaction | legacy tiêu hao key khi tạo account lỗi |
| Mọi token lưu dạng hash | lộ DB không lộ token còn hiệu lực |
| Đăng nhập sai không lộ username tồn tại qua thời gian phản hồi | legacy chỉ băm khi username tồn tại |
| Lỗi validation 422 không phản chiếu giá trị đầu vào | tránh lộ mật khẩu trong phản hồi/log |
| `/v1/auth/reset-password` có rate limit 10 lần/giờ/IP | legacy không giới hạn |
| Email mã đổi mật khẩu có mẫu riêng | legacy gửi nhầm bằng mẫu "link đặt lại" |
