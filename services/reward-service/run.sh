#!/usr/bin/env bash
# Khởi động reward-service bằng uvicorn — dùng bởi pm2 (process `ufreward`) trên VPS.
#
# QUAN TRỌNG: --workers 1. worker_manager giữ trạng thái job (WorkerManager, khoá reward_job_lock
# theo tiến trình) trong RAM của MỘT tiến trình. Chạy >1 worker sẽ có 2 WorkerManager tranh nhau
# cùng một job → phá vỡ tầng chống trùng L3 (write-ahead claim). Scale bằng nhiều máy + hàng đợi
# là việc tương lai, KHÔNG phải bằng cách tăng --workers.
set -euo pipefail

cd "$(dirname "$0")"

# Nạp .env nếu có (pm2 thường đã inject env, nhưng chạy tay vẫn cần).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PORT="${REWARD_PORT:-8422}"

# Phase 4: không còn JWT_SECRET dùng chung. Kiểm cấu hình bắt buộc sớm (fail-fast) trước khi bật uvicorn.
: "${REWARD_DATABASE_URL:?Thiếu REWARD_DATABASE_URL (DB riêng reward_db)}"
: "${REWARD_IDENTITY_URL:?Thiếu REWARD_IDENTITY_URL (API công khai của Identity)}"
: "${REWARD_TOKEN_KEY:?Thiếu REWARD_TOKEN_KEY (Fernet, mã hoá token Discord)}"

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m uvicorn reward_service.main:app \
  --host 127.0.0.1 \
  --port "${PORT}" \
  --workers 1 \
  --no-access-log \
  --proxy-headers \
  --forwarded-allow-ips '127.0.0.1'
