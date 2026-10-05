"""nginx.conf sinh từ route-map + registry: đủ route, chặn /internal, không CORS `*`, đồng bộ với gateway.

`nginx -t` chỉ chạy khi ảnh `nginx:alpine` ĐÃ CÓ SẴN (không pull); nếu không thì test báo SKIP với lý do
NOT_RUN_EXTERNAL_DEPENDENCY — SKIP không được tính là đã kiểm chứng cú pháp bằng nginx thật.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("render_nginx", ROOT / "infra/gateway/render_nginx.py")
render_nginx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_nginx)

ROUTE_MAP, REGISTRY = render_nginx.load_sources()
CONF = render_nginx.render(ROUTE_MAP, REGISTRY)
CONF_NO_TLS = render_nginx.render(ROUTE_MAP, REGISTRY, tls=False)
PREFIXES = render_nginx.collect_prefixes(ROUTE_MAP, REGISTRY)


def test_committed_nginx_conf_is_up_to_date():
    assert (ROOT / "infra/gateway/nginx.conf").read_text(encoding="utf-8") == CONF, \
        "chạy: python infra/gateway/render_nginx.py"


def test_every_route_map_prefix_and_registry_prefix_has_a_location():
    assert len(ROUTE_MAP["rules"]) >= 13 and len(PREFIXES) >= 20
    for rule in ROUTE_MAP["rules"]:
        assert f"location ^~ {rule['legacy_prefix']} {{" in CONF, rule
    for name, svc in REGISTRY["services"].items():
        for prefix in svc.get("public_prefixes") or []:
            assert f"location ^~ {prefix} {{" in CONF, (name, prefix)
    declared = re.findall(r"location \^~ (\S+) \{", CONF)
    internal_decl = [p for p in declared if p.startswith("/internal")]
    assert sorted(set(declared) - set(internal_decl)) == sorted(PREFIXES)


def test_internal_is_denied_in_multiple_layers():
    assert "location = /internal { return 404; }" in CONF
    assert "location ^~ /internal/ { return 404; }" in CONF
    assert re.search(r"if \(\$gw_bad_path\) \{\s*return 404;", CONF)
    assert '"~*^[^?]*internal" 1;' in CONF and "%25" in CONF  # cả biến thể mã hoá hai lớp
    # không có location nào proxy tới /internal
    for block in re.findall(r"location [^{]*?(/internal\S*) \{([^}]*)\}", CONF):
        assert "proxy_pass" not in block[1]
    assert not [p for p in re.findall(r"location (?:\^~|=|~\*?) (\S+)", CONF)
                if "internal" in p and not p.startswith("/internal")]


def test_no_cors_wildcard_or_any_cors_header_in_nginx():
    assert not re.search(r"Access-Control", CONF, re.IGNORECASE)
    assert not re.search(r"add_header\s+\S*[Oo]rigin\S*\s+[\"']?\*", CONF)
    assert 'Access-Control-Allow-Origin "*"' not in CONF


def test_edge_hardening_directives_present():
    for needle in ("server_tokens off;", "client_max_body_size 2m;", "client_header_timeout", "client_body_timeout",
                   "large_client_header_buffers", "limit_req_zone", "limit_req_status 429;", "limit_req zone=gw_strict",
                   "limit_req zone=gw_auth", "limit_req zone=gw_default", "proxy_connect_timeout 3s;",
                   "proxy_read_timeout", "proxy_send_timeout", "proxy_request_buffering off;",
                   "proxy_next_upstream off;", "proxy_set_header X-Real-IP $remote_addr;",
                   "proxy_set_header X-Forwarded-For $remote_addr;", 'proxy_set_header X-Service-Key "";',
                   "ssl_protocols TLSv1.2 TLSv1.3;", "ssl_certificate ", "return 301 https://",
                   "Strict-Transport-Security", "X-Content-Type-Options", "frame-ancestors 'none'"):
        assert needle in CONF, needle
    assert "$request_uri\"" not in CONF.split("log_format")[1].split(";")[0], "log nginx không ghi query"
    assert "ssl_protocols TLSv1.2 TLSv1.3;" not in CONF_NO_TLS and "add_header Strict-Transport-Security" not in CONF_NO_TLS


def test_every_proxy_location_overwrites_real_ip_and_hides_upstream_security_headers():
    blocks = re.findall(r"location [^{]+\{([^{}]*proxy_pass[^{}]*)\}", CONF)
    assert len(blocks) >= len(PREFIXES) + len(render_nginx.STRICT_PATHS)
    for b in blocks:
        assert "proxy_set_header X-Real-IP $remote_addr;" in b and 'proxy_set_header X-Service-Key "";' in b
        assert "proxy_hide_header Server;" in b and "proxy_pass http://api_gateway;" in b


def test_strict_paths_match_gateway_rate_limiter():
    from api_gateway.edge_security.ratelimit import STRICT_PATHS

    assert set(render_nginx.STRICT_PATHS) == set(STRICT_PATHS)
    for p in STRICT_PATHS:
        assert f"location = {p} {{" in CONF


def test_upstream_port_comes_from_registry():
    port = REGISTRY["services"]["api-gateway"]["port"]
    assert f"server 127.0.0.1:{port};" in CONF


def test_syntax_lint_balanced_braces_and_terminated_directives():
    depth = 0
    pending = ""
    for n, raw in enumerate(CONF.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        bare = re.sub(r"'[^']*'|\"[^\"]*\"", "", pending)  # bỏ chuỗi trong nháy trước khi đếm ngoặc
        if not bare.endswith(("{", "}", ";")):
            continue  # lệnh nhiều dòng (log_format): gom tiếp
        depth += bare.count("{") - bare.count("}")
        assert depth >= 0, f"dòng {n}: đóng thừa ngoặc"
        pending = ""
    assert pending == "" and depth == 0


@pytest.mark.parametrize("bad", ["/v1/internal/x", "/v1/a b", "/v1/a;b", "/v1/{x}", "v1/x", "/v1/$x"])
def test_unsafe_or_internal_prefixes_are_rejected_by_renderer(bad):
    with pytest.raises(ValueError):
        render_nginx.collect_prefixes({"rules": [{"legacy_prefix": bad, "target": "x"}]}, {"services": {}})


def test_nginx_syntax_check_in_container_if_image_present():
    status, detail = render_nginx.nginx_test()
    if status == "NOT_RUN_EXTERNAL_DEPENDENCY":
        pytest.skip(f"NOT_RUN_EXTERNAL_DEPENDENCY: {detail}")
    assert status == "PASS", detail
