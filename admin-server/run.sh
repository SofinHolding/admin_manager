#!/usr/bin/env bash
# Khởi chạy admin web server trên VPS.
#
# pm2 start run.sh --name ufadmin --interpreter bash
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

# Ưu tiên venv nếu có (CI/CD tạo .venv để không can thiệp Python system).
# Ngã sau mới dùng python3 của hệ thống — đủ cho dev chạy tay không cần venv.
if [ -f .venv/bin/python ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port "${ADMIN_PORT:-8421}"
