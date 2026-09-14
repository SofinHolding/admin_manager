# Triển khai Admin Manager lên VPS

## Tổng quan

| Thành phần | Nơi chạy | Địa chỉ |
|---|---|---|
| admin-server (FastAPI) | VPS — pm2 `ufadmin` | `127.0.0.1:8421` |
| admin-web (React SPA) | Phục vụ bởi admin-server | `/admin/` |
| nginx | VPS — proxy | `http://tuncon.duckdns.org/admin/` |
| CI/CD | GitHub Actions | push lên `main` → tự deploy |

---

## Lần đầu: Thiết lập thủ công

### Bước 0 — Tạo SSH key cho deployment

Chạy trên **máy local** (PowerShell):

```powershell
ssh-keygen -t ed25519 -C "github-actions-ufadmin" -f "$HOME\.ssh\ufadmin_deploy" -N ""
```

Lấy public key để thêm vào VPS:

```powershell
Get-Content "$HOME\.ssh\ufadmin_deploy.pub"
```

SSH vào VPS và thêm public key:

```bash
# Trên VPS (ssh vilas@tuncon.duckdns.org)
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "PASTE_PUBLIC_KEY_HERE" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Kiểm tra SSH hoạt động:

```powershell
ssh -i "$HOME\.ssh\ufadmin_deploy" vilas@tuncon.duckdns.org "echo OK"
```

---

### Bước 1 — Thêm GitHub Secrets

Vào **https://github.com/SofinHolding/admin_manager/settings/secrets/actions** và tạo 3 secrets:

| Secret | Giá trị |
|---|---|
| `VPS_SSH_KEY` | Nội dung file `~/.ssh/ufadmin_deploy` (private key, nhiều dòng) |
| `VPS_HOST` | `tuncon.duckdns.org` |
| `VPS_USER` | `vilas` |

Cách lấy private key:

```powershell
Get-Content "$HOME\.ssh\ufadmin_deploy" | Set-Clipboard
# Rồi dán vào ô Value của secret VPS_SSH_KEY
```

---

### Bước 2 — Chạy script setup VPS

```bash
# Từ thư mục gốc admin/ (trên máy local có SSH vào VPS)
bash scripts/setup-vps.sh
```

Script này sẽ:
1. Build `admin-web` → `dist/`
2. Tạo thư mục `/home/vilas/ufadmin/` trên VPS
3. Đẩy code lên VPS (rsync)
4. Tạo Python venv + cài deps
5. Hỏi mật khẩu Postgres → tạo `.env` (chmod 600)
6. Chạy `create_admin.py` để tạo tài khoản đăng nhập
7. Khởi động pm2 `ufadmin`
8. Thêm nginx config và reload

Sau bước này, admin có thể truy cập tại:  
👉 **http://tuncon.duckdns.org/admin/**

---

### Bước 3 — Push code lên GitHub để CI/CD tự chạy

```bash
cd admin/          # thư mục repo admin
git add .
git commit -m "Them CI/CD va setup script"
git push origin main
```

Vào tab **Actions** trên GitHub để theo dõi tiến trình.

---

## Vận hành thường ngày

```bash
# SSH vào VPS
ssh vilas@tuncon.duckdns.org

# Xem log
pm2 logs ufadmin --lines 50

# Restart
pm2 restart ufadmin

# Xem trạng thái
pm2 show ufadmin

# Deploy lại tay (không cần push)
# → Vào GitHub Actions → chọn workflow "Deploy Admin lên VPS" → Run workflow
```

---

## Cấu trúc thư mục trên VPS

```
/home/vilas/ufadmin/
  .env              ← secrets (chmod 600, KHÔNG bao giờ commit)
  .venv/            ← Python venv
  dist/             ← React build (admin-web/dist/)
  main.py
  admin_api.py
  auth.py
  viewer.py
  db.py
  stats_core.py
  create_admin.py
  requirements.txt
  run.sh
```

---

## Cập nhật mật khẩu admin sau này

```bash
ssh vilas@tuncon.duckdns.org
cd /home/vilas/ufadmin
.venv/bin/python create_admin.py   # tạo tài khoản mới hoặc đổi mật khẩu
```

---

## Gỡ lỗi

| Triệu chứng | Lệnh kiểm tra |
|---|---|
| Trang trắng / 502 | `pm2 logs ufadmin --lines 30` |
| 404 từ nginx | `sudo nginx -t && sudo journalctl -u nginx -n 20` |
| DB không kết nối được | `psql -U ufsync -d ufsync -c '\l'` |
| CI/CD thất bại | Tab Actions trên GitHub → xem step nào đỏ |

---

## Trạng thái

| Hạng mục | Trạng thái |
|---|---|
| GitHub Actions workflow | ✅ `.github/workflows/deploy.yml` |
| Script setup VPS lần đầu | ✅ `scripts/setup-vps.sh` |
| nginx config tích hợp trong script | ✅ |
| Hướng dẫn này | ✅ |
