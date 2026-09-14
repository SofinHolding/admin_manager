#!/usr/bin/env bash
# =============================================================================
#  setup-vps.sh — Thiết lập ADMIN trên VPS lần đầu (chạy tay một lần).
#
#  Chạy từ MÁY LOCAL:
#    bash scripts/setup-vps.sh
#
#  Điều kiện tiên quyết (máy local):
#    - SSH được vào vilas@tuncon.duckdns.org
#    - rsync có trong PATH
#    - Node.js >= 18 và npm có trong PATH (để build frontend)
#    - Python 3.11+ không cần thiết ở local — chỉ VPS mới cần
# =============================================================================
set -euo pipefail

VPS_USER="vilas"
VPS_HOST="tuncon.duckdns.org"
DEPLOY_DIR="/home/vilas/ufadmin"
ADMIN_PORT="8421"

# Màu sắc cho dễ đọc
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
die()  { echo -e "${RED}❌ $*${NC}"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       Setup Admin Manager lên VPS — lần đầu         ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Build frontend ─────────────────────────────────────────────────────────
echo "▶ Bước 1: Build frontend (admin-web)…"
cd "$(dirname "$0")/../admin-web"
npm ci --silent
npm run build
ok "Build xong → admin-web/dist/"
cd "$(dirname "$0")/.."

# ── 2. Tạo thư mục deploy trên VPS ───────────────────────────────────────────
echo ""
echo "▶ Bước 2: Tạo thư mục $DEPLOY_DIR trên VPS…"
ssh "$VPS_USER@$VPS_HOST" "mkdir -p $DEPLOY_DIR"
ok "Thư mục sẵn sàng"

# ── 3. Đẩy admin-server ───────────────────────────────────────────────────────
echo ""
echo "▶ Bước 3: Đẩy admin-server lên VPS…"
rsync -az --delete \
    --exclude='.env' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.db' \
    --exclude='.venv/' \
    admin-server/ \
    "$VPS_USER@$VPS_HOST:$DEPLOY_DIR/"
ok "admin-server đã đẩy"

# ── 4. Đẩy frontend dist ──────────────────────────────────────────────────────
echo ""
echo "▶ Bước 4: Đẩy frontend dist lên VPS…"
rsync -az --delete \
    admin-web/dist/ \
    "$VPS_USER@$VPS_HOST:$DEPLOY_DIR/dist/"
ok "admin-web/dist đã đẩy"

# ── 5. Thiết lập Python venv + deps trên VPS ─────────────────────────────────
echo ""
echo "▶ Bước 5: Cài Python deps trên VPS…"
ssh "$VPS_USER@$VPS_HOST" bash <<REMOTE
    set -euo pipefail
    cd $DEPLOY_DIR
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt -q
REMOTE
ok "Python deps đã cài"

# ── 6. Tạo .env trên VPS nếu chưa có ─────────────────────────────────────────
echo ""
echo "▶ Bước 6: Kiểm tra .env trên VPS…"
ENV_EXISTS=$(ssh "$VPS_USER@$VPS_HOST" "test -f $DEPLOY_DIR/.env && echo yes || echo no")
if [ "$ENV_EXISTS" = "yes" ]; then
    warn ".env đã tồn tại trên VPS — giữ nguyên, không đè."
else
    # Sinh JWT_SECRET ngẫu nhiên (32 bytes = 64 hex chars)
    JWT=$(ssh "$VPS_USER@$VPS_HOST" "python3 -c \"import secrets; print(secrets.token_hex(32))\"")

    # Lấy mật khẩu Postgres từ .env local nếu có
    DB_PASS=""
    if [ -f "admin-server/.env" ]; then
        DB_PASS=$(grep "^DATABASE_URL=" "admin-server/.env" | grep -oP 'ufsync:\K[^@]+' || true)
    fi
    if [ -z "$DB_PASS" ]; then
        echo ""
        read -rsp "  Mật khẩu Postgres của role 'ufsync' trên VPS: " DB_PASS
        echo ""
    fi

    ssh "$VPS_USER@$VPS_HOST" bash <<REMOTE
        cat > $DEPLOY_DIR/.env <<EOF
MODE=production
DATABASE_URL=postgresql://ufsync:${DB_PASS}@127.0.0.1:5432/ufsync
JWT_SECRET=${JWT}
ADMIN_PORT=${ADMIN_PORT}
ADMIN_WEB_DIR=$DEPLOY_DIR/dist
EOF
        chmod 600 $DEPLOY_DIR/.env
REMOTE
    ok ".env đã tạo (chmod 600)"
fi

# ── 7. Tạo tài khoản admin (chỉ khi lần đầu) ─────────────────────────────────
echo ""
echo "▶ Bước 7: Tạo tài khoản admin trong Postgres…"
echo ""
warn "Script sẽ mở phiên SSH tương tác để chạy create_admin.py"
warn "Nhập username/email/password cho tài khoản admin của bạn."
echo ""
ssh -t "$VPS_USER@$VPS_HOST" "cd $DEPLOY_DIR && .venv/bin/python create_admin.py"

# ── 8. Khởi động pm2 ──────────────────────────────────────────────────────────
echo ""
echo "▶ Bước 8: Khởi động dịch vụ bằng pm2…"
ssh "$VPS_USER@$VPS_HOST" bash <<REMOTE
    set -euo pipefail
    cd $DEPLOY_DIR
    chmod +x run.sh
    if pm2 list | grep -q ufadmin; then
        pm2 restart ufadmin
        echo "Đã restart pm2 ufadmin"
    else
        pm2 start run.sh --name ufadmin --interpreter bash
        pm2 save
        echo "Đã start pm2 ufadmin lần đầu"
    fi
REMOTE
ok "pm2 ufadmin đang chạy"

# ── 9. Cấu hình nginx ─────────────────────────────────────────────────────────
echo ""
echo "▶ Bước 9: Thêm nginx config…"
NGINX_CONF="/etc/nginx/sites-available/ufadmin"
NGINX_CONF_EXISTS=$(ssh "$VPS_USER@$VPS_HOST" "test -f $NGINX_CONF && echo yes || echo no")
if [ "$NGINX_CONF_EXISTS" = "yes" ]; then
    warn "nginx config đã tồn tại ($NGINX_CONF) — bỏ qua."
else
    ssh "$VPS_USER@$VPS_HOST" bash <<REMOTE
        sudo tee $NGINX_CONF > /dev/null <<'NGINX'
# Admin Manager — proxy tới admin-server (port 8421)
# Đặt trong /etc/nginx/sites-available/ufadmin
# Kích hoạt: sudo ln -s /etc/nginx/sites-available/ufadmin /etc/nginx/sites-enabled/ufadmin
#
# Truy cập: http://tuncon.duckdns.org/admin/
# (hoặc thêm server block riêng với server_name admin.tuncon.duckdns.org nếu dùng subdomain)

server {
    listen 80;
    server_name tuncon.duckdns.org;

    # Đẩy /admin → admin-server để SPA xử lý routing.
    # trailing slash trong proxy_pass cắt prefix /admin trước khi chuyển tiếp.
    location /admin/ {
        proxy_pass         http://127.0.0.1:${ADMIN_PORT}/admin/;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        # Timeout dài hơn mặc định — tránh 504 khi Postgres chậm lần đầu.
        proxy_read_timeout 30s;
    }

    # API endpoint cũng qua admin-server
    location /v1/auth/ {
        proxy_pass http://127.0.0.1:${ADMIN_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    location /v1/viewer/ {
        proxy_pass http://127.0.0.1:${ADMIN_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    location /v1/admin-api/ {
        proxy_pass http://127.0.0.1:${ADMIN_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
NGINX

        sudo ln -sf $NGINX_CONF /etc/nginx/sites-enabled/ufadmin
        sudo nginx -t && sudo systemctl reload nginx
REMOTE
    ok "nginx config đã tạo và reload"
fi

# ── 10. Kiểm tra cuối ─────────────────────────────────────────────────────────
echo ""
echo "▶ Bước 10: Kiểm tra health…"
sleep 2   # Chờ uvicorn khởi động
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://$VPS_HOST/admin/" || echo "000")
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "304" ]; then
    ok "Truy cập http://$VPS_HOST/admin/ trả $HTTP_CODE — OK!"
else
    warn "Truy cập trả HTTP $HTTP_CODE. Kiểm tra log: ssh $VPS_USER@$VPS_HOST 'pm2 logs ufadmin --lines 30'"
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   🎉 Setup xong!                                     ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  🌐 Admin URL: http://$VPS_HOST/admin/             ║"
echo "║  📝 Log:  pm2 logs ufadmin                          ║"
echo "║  🔄 CI/CD: push lên main → tự deploy               ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Bước tiếp theo:"
echo "  1. Thêm GitHub Secrets (xem DEPLOY.md)"
echo "  2. Push commit này lên main → CI/CD sẽ tự chạy"
