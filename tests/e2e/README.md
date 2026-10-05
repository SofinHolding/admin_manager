# End-to-end tests

Chạy qua Gateway với đủ service và hạ tầng thật (Postgres, Redis). Chưa có test e2e vì chưa có gateway và
nghiệp vụ; bộ test sẽ được thêm theo phase. Mục tiêu nghiệm thu:

| # | Kịch bản | Phase |
|---|---|---|
| E1 | Tool X mới: cấp credential → handshake → đăng ký resource → snapshot → metrics, không sửa Manager | 2 |
| E2 | Đẩy bài rồi **đẩy lại bài cũ với views mới**: Dashboard cập nhật ngày đăng cũ | 3 |
| E3 | Dừng analytics: login/register/ingest vẫn chạy; Dashboard báo stale thay vì chết | 3 |
| E4 | Dừng reward: Auth, Dashboard, Integration không ảnh hưởng | 4 |
| E5 | Dừng identity: token còn hạn vẫn verify offline, service khác không crash | 1.4 |
| E6 | Website B (Relying Party): đăng nhập PKCE, khóa tài khoản thì bị chặn đúng thời hạn | 1.6 |

Phiên bản thu nhỏ không cần hạ tầng đã chạy ở `tests/integration/test_process_isolation.py`.
