#!/usr/bin/env python3
"""Sinh `infra/gateway/nginx.conf` từ NGUỒN SỰ THẬT: `route-map.json` + `service-registry.json`.

nginx là lớp TLS/biên ngoài cùng; nó chuyển MỌI route công khai tới `api-gateway` (127.0.0.1:8400) — gateway mới là
nơi định tuyến theo service. Mọi tiền tố trong route-map và `public_prefixes` của registry đều có một `location`
tường minh; đường dẫn không khai báo ⇒ 404. `/internal` bị chặn ở nginx VÀ ở gateway (phòng thủ nhiều lớp).

CORS do GATEWAY quản (allowlist chính xác). nginx KHÔNG thêm header CORS nào (tránh trùng lặp, tránh `*`).

Cách dùng:
    python infra/gateway/render_nginx.py                 # ghi infra/gateway/nginx.conf
    python infra/gateway/render_nginx.py --check         # exit 1 nếu nginx.conf đã lỗi thời so với nguồn
    python infra/gateway/render_nginx.py --nginx-test    # nginx -t trong container tạm (chỉ khi ảnh nginx:alpine
                                                         # ĐÃ CÓ SẴN; không pull) — nếu không: NOT_RUN_EXTERNAL_DEPENDENCY

Chỉ SINH FILE. Việc áp dụng lên VPS là PENDING_PRODUCTION_APPROVAL (cần duyệt riêng, xem docs/operations).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ROUTE_MAP = HERE / "route-map.json"
REGISTRY = ROOT / "infra" / "service-registry.json"
OUTPUT = HERE / "nginx.conf"

# Đường dẫn "nhạy cảm" (đoán mật khẩu/spam email) — khớp STRICT_PATHS của gateway (có test đồng bộ).
STRICT_PATHS = (
    "/v1/auth/login", "/v1/auth/register", "/v1/auth/forgot-password", "/v1/auth/reset-password",
    "/v1/auth/resend-verify", "/v1/auth/validate-key", "/v1/auth/check-username", "/v1/auth/check-email",
    "/v1/auth/change-password", "/v1/auth/change-password/send-code", "/v1/auth/verify-email",
    "/v1/reward/auth/login", "/oauth/token",
)
AUTH_PREFIXES = ("/v1/auth", "/v1/reward/auth", "/oauth")
GATEWAY_PORT_FALLBACK = 8400


def collect_prefixes(route_map: dict, registry: dict) -> list[str]:
    """Hợp các tiền tố công khai: route-map (legacy) + public_prefixes của registry; bỏ trùng, giữ thứ tự ổn định."""
    seen: dict[str, None] = {}
    for rule in route_map["rules"]:
        seen.setdefault(rule["legacy_prefix"])
    for svc in registry["services"].values():
        for prefix in svc.get("public_prefixes") or []:
            seen.setdefault(prefix)
    prefixes = sorted(seen)
    for p in prefixes:
        if not p.startswith("/") or "internal" in p.lower() or any(c in p for c in " \t\n;{}\"'$\\"):
            raise ValueError(f"tiền tố route không hợp lệ/không an toàn cho nginx: {p!r}")
    return prefixes


def _security_headers(tls: bool, indent: str) -> str:
    lines = [
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
        'add_header Referrer-Policy "no-referrer" always;',
        "add_header Content-Security-Policy \"default-src 'none'; frame-ancestors 'none'\" always;",
    ]
    if tls:
        lines.append('add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;')
    return "\n".join(indent + ln for ln in lines)


_HIDE = ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy",
         "Strict-Transport-Security", "Server", "X-Powered-By")


def _proxy_block(tls: bool, zone: str | None, burst: int, indent: str = "        ") -> str:
    out: list[str] = []
    if zone:
        out.append(f"limit_req zone={zone} burst={burst} nodelay;")
    out += [
        "proxy_pass http://api_gateway;",
        "proxy_http_version 1.1;",
        'proxy_set_header Connection "";',
        'proxy_set_header Upgrade "";',
        "proxy_set_header Host $host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $remote_addr;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
        "proxy_set_header X-Request-Id $gw_request_id;",
        'proxy_set_header X-Service-Key "";',
        "proxy_request_buffering off;",
        "proxy_buffering off;",
        "proxy_connect_timeout 3s;",
        "proxy_send_timeout 60s;",
        "proxy_read_timeout 60s;",
        "proxy_next_upstream off;",
    ]
    out += [f"proxy_hide_header {h};" for h in _HIDE]
    body = "\n".join(indent + ln for ln in out)
    return body + "\n" + _security_headers(tls, indent)


def render(route_map: dict, registry: dict, *, tls: bool = True, server_name: str = "_",
           cert: str = "/etc/nginx/tls/fullchain.pem", key: str = "/etc/nginx/tls/privkey.pem",
           max_body: str = "2m") -> str:
    prefixes = collect_prefixes(route_map, registry)
    gw = registry["services"]["api-gateway"]
    gw_port = int(gw.get("port", GATEWAY_PORT_FALLBACK))
    listen = "listen 443 ssl;\n        http2 on;" if tls else "listen 80;"

    parts: list[str] = []
    add = parts.append
    add(f"""# =============================================================================================
# TỆP SINH TỰ ĐỘNG bởi infra/gateway/render_nginx.py — KHÔNG sửa tay. Sửa route-map.json / service-registry.json
# rồi chạy lại script. Chưa áp dụng lên VPS (PENDING_PRODUCTION_APPROVAL).
# Gateway (127.0.0.1:{gw_port}) phải chạy với GATEWAY_TRUSTED_PROXIES=127.0.0.1 để tin X-Real-IP do nginx đặt.
# CORS do gateway quản (allowlist chính xác) — nginx KHÔNG thêm bất kỳ header CORS nào.
# =============================================================================================
worker_processes auto;
pid /tmp/nginx.pid;

events {{
    worker_connections 1024;
}}

http {{
    server_tokens off;
    default_type application/json;
    charset utf-8;

    # Log KHÔNG ghi query string ($request_uri) và không ghi header/cookie/body: tránh lộ token.
    log_format gw_redacted '$remote_addr [$time_iso8601] "$request_method $uri" $status $body_bytes_sent '
                           'rt=$request_time rid=$gw_request_id';
    access_log /var/log/nginx/access.log gw_redacted;
    error_log /var/log/nginx/error.log warn;

    # Giới hạn kích thước/thời gian (chống slowloris, body khổng lồ, header khổng lồ).
    client_max_body_size {max_body};
    client_header_timeout 10s;
    client_body_timeout 30s;
    send_timeout 30s;
    keepalive_timeout 15s;
    client_header_buffer_size 4k;
    large_client_header_buffers 4 8k;
    merge_slashes on;

    # Rate limit nền theo IP (gateway còn lớp riêng; KHÔNG thay rate limit của service).
    limit_req_zone $binary_remote_addr zone=gw_strict:10m rate=10r/m;
    limit_req_zone $binary_remote_addr zone=gw_auth:10m rate=60r/m;
    limit_req_zone $binary_remote_addr zone=gw_default:10m rate=600r/m;
    limit_req_status 429;
    limit_conn_zone $binary_remote_addr zone=gw_conn:10m;

    # Giữ X-Request-Id hợp lệ của client, ngược lại dùng id do nginx sinh.
    map $http_x_request_id $gw_request_id {{
        "~^[A-Za-z0-9_.\\-]{{8,128}}$" $http_x_request_id;
        default $request_id;
    }}

    # Chặn /internal mọi biến thể trên phần PATH (không gồm query): chữ thường/hoa, mã hoá %69/%49,
    # mã hoá hai lớp (%25), dấu \\ mã hoá, NUL. Gateway cũng tự giải mã lặp và chặn lại (2 lớp).
    map $request_uri $gw_bad_path {{
        default 0;
        "~*^[^?]*internal" 1;
        "~*^[^?]*%25" 1;
        "~*^[^?]*%(49|69)" 1;
        "~*^[^?]*%5c" 1;
        "~*^[^?]*%00" 1;
    }}

    upstream api_gateway {{
        server 127.0.0.1:{gw_port};
        keepalive 32;
    }}
""")
    if tls:
        add("""
    server {
        listen 80;
        server_name %s;
        return 301 https://$host$request_uri;
    }
""" % server_name)

    add(f"""
    server {{
        {listen}
        server_name {server_name};
""")
    if tls:
        add(f"""
        ssl_certificate {cert};
        ssl_certificate_key {key};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 1d;
        ssl_session_tickets off;
""")
    add(f"""
        limit_conn gw_conn 50;

        # Thông báo lỗi do nginx sinh dùng cùng envelope với gateway.
        error_page 404 = @gw_not_found;
        error_page 405 = @gw_method;
        error_page 413 = @gw_too_large;
        error_page 414 431 = @gw_bad_request;
        error_page 429 = @gw_rate_limited;

        if ($request_method !~ ^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)$) {{
            return 405;
        }}

        # /internal/* KHÔNG BAO GIỜ được proxy (service-auth-contract-v1 §5).
        if ($gw_bad_path) {{
            return 404;
        }}
        location = /internal {{ return 404; }}
        location ^~ /internal/ {{ return 404; }}

        # Chỉ liveness công khai; /health/ready, /health/version, /health/upstreams không ra ngoài.
        location = /health/live {{
{_proxy_block(tls, None, 0)}
        }}
""")
    add("\n        # ── Đường dẫn nhạy cảm (đoán mật khẩu/spam email): giới hạn chặt ──")
    for path in STRICT_PATHS:
        add(f"        location = {path} {{\n{_proxy_block(tls, 'gw_strict', 5)}\n        }}")
    add("\n        # ── Tiền tố công khai: sinh từ route-map.json + service-registry.json ──")
    for prefix in prefixes:
        is_auth = any(prefix == a or prefix.startswith(a + "/") for a in AUTH_PREFIXES)
        zone = "gw_auth" if is_auth else "gw_default"
        burst = 20 if is_auth else 100
        add(f"        location ^~ {prefix} {{\n{_proxy_block(tls, zone, burst)}\n        }}")
    sec = _security_headers(tls, "            ")
    add(f"""
        # Mọi đường dẫn khác: 404 (không lộ cấu hình).
        location / {{
            return 404;
        }}

        location @gw_not_found {{
            internal;
{sec}
            add_header Cache-Control "no-store" always;
            return 404 '{{"ok":false,"error":{{"code":"NOT_FOUND","message":"Not Found","details":{{}}}},"meta":{{"request_id":"$gw_request_id"}}}}';
        }}
        location @gw_method {{
            internal;
{sec}
            add_header Allow "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS" always;
            return 405 '{{"ok":false,"error":{{"code":"METHOD_NOT_ALLOWED","message":"Method not allowed","details":{{}}}},"meta":{{"request_id":"$gw_request_id"}}}}';
        }}
        location @gw_too_large {{
            internal;
{sec}
            add_header Connection "close" always;
            return 413 '{{"ok":false,"error":{{"code":"REQUEST_VALIDATION_FAILED","message":"Request body too large","details":{{"reason":"body_too_large"}}}},"meta":{{"request_id":"$gw_request_id"}}}}';
        }}
        location @gw_bad_request {{
            internal;
{sec}
            return 400 '{{"ok":false,"error":{{"code":"REQUEST_VALIDATION_FAILED","message":"Malformed request","details":{{}}}},"meta":{{"request_id":"$gw_request_id"}}}}';
        }}
        location @gw_rate_limited {{
            internal;
{sec}
            add_header Retry-After "6" always;
            return 429 '{{"ok":false,"error":{{"code":"RATE_LIMITED","message":"Too many requests","details":{{}}}},"meta":{{"request_id":"$gw_request_id"}}}}';
        }}
    }}
}}
""")
    return "\n".join(parts).replace("\r\n", "\n")


def load_sources() -> tuple[dict, dict]:
    return (json.loads(ROUTE_MAP.read_text(encoding="utf-8")),
            json.loads(REGISTRY.read_text(encoding="utf-8")))


def _docker_image_present(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def nginx_test(image: str = "nginx:alpine") -> tuple[str, str]:
    """('PASS'|'FAIL'|'NOT_RUN_EXTERNAL_DEPENDENCY', chi tiết). KHÔNG BAO GIỜ pull ảnh."""
    if not _docker_image_present(image):
        return "NOT_RUN_EXTERNAL_DEPENDENCY", f"ảnh {image} chưa có sẵn (hoặc Docker không chạy); không pull theo quy định"
    route_map, registry = load_sources()
    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp) / "nginx.conf"
        # `nginx -t` kiểm cú pháp/ngữ nghĩa; bản HTTP-only tránh phụ thuộc chứng chỉ TLS thật.
        conf.write_text(render(route_map, registry, tls=False), encoding="utf-8")
        cmd = ["docker", "run", "--rm", "--name", "mp-dev-nginx-t", "-v", f"{conf}:/etc/nginx/nginx.conf:ro",
               image, "nginx", "-t"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return ("PASS" if r.returncode == 0 else "FAIL"), (r.stdout + r.stderr).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUTPUT)
    ap.add_argument("--check", action="store_true", help="so sánh với tệp đã sinh; exit 1 nếu lỗi thời")
    ap.add_argument("--nginx-test", action="store_true", help="chạy nginx -t trong container tạm (không pull)")
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--server-name", default="_")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    route_map, registry = load_sources()
    text = render(route_map, registry, tls=not args.no_tls, server_name=args.server_name)
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print("nginx.conf đã lỗi thời so với route-map/registry — chạy lại render_nginx.py", file=sys.stderr)
            return 1
        print("nginx.conf khớp nguồn")
        return 0
    if args.nginx_test:
        status, detail = nginx_test()
        print(f"{status}: {detail}")
        return 1 if status == "FAIL" else 0
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"đã ghi {args.out} ({len(collect_prefixes(route_map, registry))} tiền tố, {len(STRICT_PATHS)} đường dẫn chặt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
