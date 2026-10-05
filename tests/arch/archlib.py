"""Thư viện dùng chung cho test kiến trúc (đọc registry, phân tích import và SQL bằng AST).

Test kiến trúc là CỔNG chất lượng: vi phạm ranh giới service thì CI đỏ, không phụ thuộc review của người.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "infra" / "service-registry.json"

# Tên bảng được phép trùng giữa các service: mỗi service giữ BẢN SAO RIÊNG trong DB riêng của nó
# (outbox của chính nó; account_projection là read model cục bộ nuôi bằng event, không phải bảng accounts).
PER_SERVICE_COPIES = {"outbox", "account_projection"}

_SQL_TABLE_REF = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?)\s+"
                            r"(?:[a-z_]+\.)?([a-z_][a-z0-9_]*)\b", re.I)
_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[a-z_]+\.)?([a-z_][a-z0-9_]*)", re.I)


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def scaffold_services(registry: dict | None = None) -> dict[str, dict]:
    registry = registry or load_registry()
    return {n: s for n, s in registry["services"].items() if s["status"] == "scaffold"}


def python_files(*roots: Path) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if root.exists():
            out += [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(out)


def top_level_imports(source: str) -> set[str]:
    """Tên package cấp cao nhất được import tuyệt đối. Import tương đối (`from . import x`) bị bỏ qua."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def forbidden_imports(source: str, own_package: str | None, registry: dict | None = None) -> list[str]:
    """Các import vi phạm ranh giới: package của service khác hoặc module legacy."""
    registry = registry or load_registry()
    other_packages = {s["package"] for s in registry["services"].values()} - {own_package}
    forbidden = other_packages | set(registry["forbidden_legacy_modules"])
    return sorted(top_level_imports(source) & forbidden)


def string_constants(source: str) -> list[str]:
    return [n.value for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def foreign_table_refs(source: str, own_service: str, registry: dict | None = None) -> set[str]:
    """Bảng của service KHÁC được tham chiếu trong chuỗi SQL (SELECT/JOIN/INSERT/UPDATE...)."""
    registry = registry or load_registry()
    foreign: set[str] = set()
    for name, svc in registry["services"].items():
        if name != own_service:
            foreign |= {t for t in svc["owned_tables"] if "*" not in t}
    own = {t for t in registry["services"][own_service]["owned_tables"]} | PER_SERVICE_COPIES
    hits: set[str] = set()
    for text in string_constants(source):
        if not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b", text, re.I):
            continue
        for m in _SQL_TABLE_REF.finditer(text):
            table = m.group(1).lower()
            if table in foreign and table not in own:
                hits.add(table)
    return hits


def created_tables(sql: str) -> set[str]:
    return {m.group(1).lower() for m in _CREATE_TABLE.finditer(sql)}


def module_header(init_py: Path) -> dict[str, str]:
    """Đọc khối `key: value` ở docstring đầu `__init__.py` của module."""
    doc = ast.get_docstring(ast.parse(init_py.read_text(encoding="utf-8"))) or ""
    header: dict[str, str] = {}
    for line in doc.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            header[key.strip()] = value.strip()
    return header


def stdlib_names() -> set[str]:
    return set(sys.stdlib_module_names)
