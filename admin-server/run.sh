#!/usr/bin/env bash
# Khởi chạy admin web server trên VPS.
#
# pm2 start run.sh --name ufadmin
# pm2 save
#
# Yêu cầu: file .env cùng thư mục chứa biến môi trường (xem .env.example).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

exec python -m uvicorn main:app --host 0.0.0.0 --port "${ADMIN_PORT:-8421}"
