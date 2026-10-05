"""Ranh giới của gateway: không import service/legacy khác, không DB, không xác thực nghiệp vụ, không route /internal."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "api_gateway"
ROOT = Path(__file__).resolve().parents[3]
REGISTRY = json.loads((ROOT / "infra/service-registry.json").read_text(encoding="utf-8"))

OTHER_SERVICE_PACKAGES = {s["package"] for n, s in REGISTRY["services"].items() if n != "api-gateway"}
LEGACY = set(REGISTRY["forbidden_legacy_modules"])
# Gateway KHÔNG làm: xác thực JWT/crypto nghiệp vụ, DB, Redis, SDK verify của service.
FORBIDDEN_CAPABILITY_IMPORTS = {"jwt", "jose", "jwcrypto", "authlib", "cryptography", "nacl", "bcrypt", "passlib",
                                "manager_sdk", "asyncpg", "psycopg", "psycopg2", "sqlalchemy", "sqlite3",
                                "redis", "aioredis", "pymongo"}
ALLOWED_THIRD_PARTY = {"httpx", "fastapi", "starlette", "uvicorn", "manager_observability", "manager_contracts",
                       "pydantic", "anyio", "h11", "httptools"}


def imports_of(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def violations(source: str) -> list[str]:
    stdlib = set(sys.stdlib_module_names)
    bad = []
    for name in sorted(imports_of(source)):
        if name in OTHER_SERVICE_PACKAGES or name in LEGACY:
            bad.append(f"{name} (service khác/legacy)")
        elif name in FORBIDDEN_CAPABILITY_IMPORTS:
            bad.append(f"{name} (năng lực không thuộc gateway)")
        elif name not in stdlib and name not in ALLOWED_THIRD_PARTY and name != "api_gateway":
            bad.append(f"{name} (không nằm trong danh sách cho phép)")
    return bad


SOURCES = sorted(SRC.rglob("*.py"))


def test_sources_found():
    assert len(SOURCES) >= 15


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(SRC)))
def test_gateway_source_respects_boundaries(path):
    assert violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", sorted((Path(__file__).parent).glob("*.py")), ids=lambda p: p.name)
def test_gateway_tests_do_not_import_other_services(path):
    bad = [n for n in imports_of(path.read_text(encoding="utf-8")) if n in OTHER_SERVICE_PACKAGES or n in LEGACY]
    assert bad == []


def test_checker_detects_violations():
    """Test của test: bộ kiểm phải bắt được vi phạm thật."""
    assert violations("import jwt\n") != []
    assert violations("from identity_service.auth import login\n") != []
    assert violations("import asyncpg\n") != []
    assert violations("from manager_sdk import TokenVerifier\n") != []
    assert violations("import stats_core\n") != []
    assert violations("import somethingnew\n") != []
    assert violations("import json\nimport httpx\nfrom api_gateway.config import Settings\n") == []


def test_no_business_authentication_logic_in_gateway():
    text = "\n".join(p.read_text(encoding="utf-8") for p in SOURCES)
    for needle in ("jwt.decode", "verify_token", "TokenVerifier", "require_user", "require_role", "require_perm",
                   "jwks", "ServiceKeyAuth", "hmac.compare_digest", "RS256"):
        assert needle.lower() not in text.lower(), f"gateway không được chứa logic xác thực nghiệp vụ: {needle}"


def test_gateway_only_forwards_authorization_untouched():
    from api_gateway.edge_security.headers import sanitize_request_headers

    out = sanitize_request_headers([(b"authorization", b"Bearer abc.def")], client_ip="1.2.3.4",
                                   scheme="http", request_id="req_12345678")
    assert (b"authorization", b"Bearer abc.def") in out


def test_service_key_is_only_ever_mentioned_to_strip_it():
    hits = [(p.name, ln.strip()) for p in SOURCES for ln in p.read_text(encoding="utf-8").splitlines()
            if re.search(r"x-service-key", ln, re.IGNORECASE) and not ln.lstrip().startswith(("#", '"""', "-"))]
    assert all(name == "headers.py" for name, _ in hits), hits


def test_registry_declares_gateway_without_database_and_with_matching_modules():
    gw = REGISTRY["services"]["api-gateway"]
    assert gw["database"] is None and gw["port"] == 8400 and gw["public_prefixes"] == []
    on_disk = {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert on_disk == set(gw["modules"]) == {"routing", "edge_security", "access_log"}


def test_no_route_in_route_map_or_registry_points_into_internal():
    route_map = json.loads((ROOT / "infra/gateway/route-map.json").read_text(encoding="utf-8"))
    prefixes = [r["legacy_prefix"] for r in route_map["rules"]]
    prefixes += [p for s in REGISTRY["services"].values() for p in (s.get("public_prefixes") or [])]
    assert prefixes and not [p for p in prefixes if "internal" in p.lower()]
