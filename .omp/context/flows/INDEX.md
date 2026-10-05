# Flow Index

## Core

- [login-auth.md](login-auth.md) — Đăng nhập/đăng xuất/refresh token, cả admin-server lẫn proxy qua reward-service.
- [dashboard-stats.md](dashboard-stats.md) — Đọc thống kê viewer, rẽ nhánh dev (JSON local Tool)/production (Postgres `documents` do Tool ghi), cache Redis.

## Supporting

- [reward-job-lifecycle.md](reward-job-lifecycle.md) — Vòng đời 1 reward job: tạo → validate → chạy → xác nhận → phân phối → pause/stop → recovery sau crash.
- [discord-command-flow.md](discord-command-flow.md) — Cơ chế gọi slash command qua Discord Gateway/REST + xác nhận kết quả (chi tiết bên trong bước "chạy" của reward-job-lifecycle).

## Background

- Recovery orphaned attempts (`reward-service/engine/recovery.py`) chạy lúc `lifespan` boot — xem mục "Recovery sau crash tiến trình" trong `reward-job-lifecycle.md`, không tách file riêng vì chỉ ~10 dòng logic.
