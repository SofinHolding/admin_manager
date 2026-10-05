# api-gateway

Điểm vào công khai duy nhất: route theo route-map, bảo mật biên (CORS allowlist, header, giới hạn body, rate limit). Không chứa nghiệp vụ.

| | |
|---|---|
| Cổng | 8400 |
| Database (riêng) | `—` |
| Package | `api_gateway` |
| Trạng thái | scaffold (chưa có nghiệp vụ; module được hiện thực theo phase) |

## Module

| Module | Phase | Trách nhiệm |
|---|---|---|
| `routing` | 5 | route theo infra/gateway/route-map.json, proxy streaming, không bao giờ route /internal |
| `edge_security` | 5 | security headers, CORS allowlist, giới hạn kích thước body, rate limit nền, xoá header nhạy cảm |
| `access_log` | 5 | request-id, access log đã che secret, tách health khỏi traffic công khai |

## Dữ liệu và sự kiện (theo `infra/service-registry.json`)

- Bảng sở hữu: —
- Phát event: —
- Nhận event: —

## Chạy

```bash
cd services/api-gateway
python -m pytest -q                  # test riêng của service
python -m uvicorn api_gateway.main:app --port 8400
```

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
