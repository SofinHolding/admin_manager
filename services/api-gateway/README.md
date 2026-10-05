# api-gateway

Điểm vào công khai duy nhất của hệ thống: reverse proxy **streaming** (Starlette ASGI thuần + httpx) định tuyến theo
`infra/gateway/route-map.json` + `public_prefixes` trong `infra/service-registry.json`, kèm bảo mật biên.
**Không chứa nghiệp vụ**: không verify JWT, không DB, chỉ chuyển `Authorization` nguyên vẹn cho service đích.

| | |
|---|---|
| Cổng | 8400 (bind `127.0.0.1` khi chạy `python -m api_gateway.main`) |
| Database (riêng) | — (không có) |
| Package | `api_gateway` |
| Trạng thái | Phase 5 đã hiện thực; **chưa triển khai** (nginx/VPS: `PENDING_PRODUCTION_APPROVAL`) |

## Module

| Module | Trách nhiệm |
|---|---|
| `routing` | `pathnorm` (chuẩn hoá path + chặn `/internal` mọi biến thể), `table` (bảng route từ route-map/registry, khớp tiền tố dài nhất tại ranh giới segment), `proxy` (stream request/response, 502/504, timeout), `breaker` (circuit breaker mỗi upstream) |
| `edge_security` | `headers` (header an toàn, loại header nguy hiểm/hop-by-hop, IP client), `cors` (allowlist chính xác), `limits` (header/URL/Content-Length/method), `ratelimit` (token bucket IP+nhóm route) |
| `access_log` | `logger` (request-id + log JSON mỗi request), `redact` (che secret) |

Thứ tự xử lý: request-id → IP client → path chuẩn hoá + **chặn `/internal` (404)** → kiểm head (method/header/URL/CL) →
health / `/v1/health` → định tuyến (404 nếu không có) → CORS preflight → rate limit → breaker → proxy streaming.

## Hành vi chính

- **`/internal/*` không bao giờ được proxy**: mọi biến thể (`//internal`, `/INTERNAL`, `/%69nternal`, mã hoá nhiều lớp,
  `/v1/../internal`, `;param`, NFKC…) ⇒ 404. Gateway định tuyến và chuyển tiếp CÙNG một chuỗi đã chuẩn hoá nên không có
  khác biệt phân tích với upstream. Bất kỳ segment nào bằng `internal` (kể cả `/v1/x/internal`) đều bị chặn.
- **Body**: `GATEWAY_MAX_BODY_BYTES` (mặc định 2 MiB). Vượt `Content-Length` ⇒ 413 không chạm upstream; chunked ⇒ 413
  ngay khi đếm vượt (không chờ hết stream). Chờ client gửi tiếp quá `GATEWAY_BODY_TIMEOUT_S` ⇒ 408.
- **Upstream lỗi**: connect timeout ngắn (2s) ⇒ 502/504 `UNAVAILABLE` envelope; sau N lỗi liên tiếp mạch mở ⇒ 503 +
  `Retry-After` không gọi upstream; nửa-mở cho 1 request thăm dò. Upstream đứt giữa chừng khi đã gửi một phần ⇒ đóng kết
  nối (không cụt-như-hoàn-chỉnh). Không retry, không theo redirect, không đọc proxy từ môi trường.
- **Header**: loại `X-Service-Key`, `X-Real-IP`, `X-Forwarded-*`, `Forwarded`, `X-Internal-*`, `X-Gateway-*`, method-override,
  hop-by-hop; đặt lại `X-Real-IP`/`X-Forwarded-For` = IP peer (hoặc IP do **proxy tin cậy** đặt, xem `GATEWAY_TRUSTED_PROXIES`).
  Response: bỏ `Server`/`X-Powered-By`/CORS của upstream; thêm `nosniff`, `X-Frame-Options`, CSP `frame-ancestors 'none'`,
  `Referrer-Policy`, `Cache-Control: no-store` cho nhóm auth (không ghi đè cache của JWKS), HSTS khi production/https.
- **CORS**: `GATEWAY_CORS_ORIGINS` exact-match; `*`/`null`/có đường dẫn ⇒ lỗi cấu hình. Origin lạ ⇒ không có header CORS.
- **Rate limit nền** theo IP + nhóm (`strict`: login/register/reset/verify…, `auth`, `default`) ⇒ 429 + `Retry-After`.
  Không thay rate limit của service.
- **Log** (`gateway.access`, JSON): method, path (đã che), query (khoá giữ, giá trị nhạy cảm `[REDACTED]`), status,
  latency_ms, upstream, request_id. **Không bao giờ** log Authorization/cookie/body. Health log ở mức DEBUG.
- **Health**: `/health/live`, `/health/ready` (chỉ kiểm cấu hình gateway, không gọi upstream), `/health/version`;
  `/health/upstreams` chỉ trả lời khi gọi trực tiếp từ loopback, không có header chuyển tiếp (qua nginx ⇒ 404).
  `/v1/health` (legacy) do gateway tự trả.

## Cấu hình (`.env.example`)

`GATEWAY_ENV`, `GATEWAY_CORS_ORIGINS`, `GATEWAY_CORS_ALLOW_CREDENTIALS`, `GATEWAY_MAX_BODY_BYTES`, `GATEWAY_MAX_HEADER_BYTES`,
`GATEWAY_MAX_HEADER_COUNT`, `GATEWAY_MAX_URL_BYTES`, `GATEWAY_UPSTREAM_{IDENTITY,INTEGRATION,ANALYTICS,REWARD}`,
`GATEWAY_{CONNECT,READ,BODY}_TIMEOUT_S`, `GATEWAY_BREAKER_{THRESHOLD,COOLDOWN_S}`, `GATEWAY_RATE_LIMIT_*`,
`GATEWAY_TRUSTED_PROXIES`, `GATEWAY_ROUTE_MAP_PATH`, `GATEWAY_REGISTRY_PATH`. Sai cấu hình ⇒ `ConfigError` (fail-fast).

## Chạy

```bash
cd services/api-gateway
python -m pytest -q -p no:cacheprovider      # test riêng (upstream echo thật, không cần Postgres)
python -m api_gateway.main                    # uvicorn, proxy_headers/server_header/access_log đều TẮT có chủ đích
```

Khi chạy bằng lệnh `uvicorn` thủ công PHẢI thêm `--no-proxy-headers --no-server-header --no-access-log`
(log truy cập gốc của uvicorn ghi nguyên query ⇒ lộ token).

## nginx (production)

`python infra/gateway/render_nginx.py` sinh `infra/gateway/nginx.conf` từ cùng route-map + registry (TLS, header, `limit_req`,
`client_max_body_size`, deny `/internal`, `X-Real-IP`, timeout). `--check` kiểm tệp đã sinh còn khớp nguồn;
`--nginx-test` chạy `nginx -t` trong container tạm **chỉ khi ảnh `nginx:alpine` đã có sẵn** (không pull), nếu không thì in
`NOT_RUN_EXTERNAL_DEPENDENCY`. Việc áp dụng lên VPS cần duyệt riêng (`PENDING_PRODUCTION_APPROVAL`). Khi đặt sau nginx
cùng máy, chạy gateway với `GATEWAY_TRUSTED_PROXIES=127.0.0.1`.

## Giới hạn đã biết

- Rate limit và circuit breaker nằm trong bộ nhớ của MỘT tiến trình (nhiều worker ⇒ hạn mức nhân lên); đủ cho lớp chặn thô
  ở biên, nginx `limit_req` là lớp ngoài.
- Slowloris ở giai đoạn header do HTTP server (uvicorn/nginx) xử lý, không phải gateway; thân request có timeout riêng.
- Không hỗ trợ WebSocket/HTTP upgrade (bị từ chối).
- Không có phiên bản route theo `X-Contract-Version`: header được chuyển nguyên cho service đích quyết định.
- Quy tắc "segment `internal` ở bất kỳ vị trí nào bị chặn" sẽ chặn cả tham số đường dẫn tình cờ có giá trị `internal`.

Quy tắc ràng buộc: xem `CLAUDE.md` ở thư mục gốc. Không import service khác, không đụng DB của service khác.
