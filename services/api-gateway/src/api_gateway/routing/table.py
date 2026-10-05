"""Bảng định tuyến dựng từ `infra/gateway/route-map.json` + `public_prefixes` trong `infra/service-registry.json`.

Khớp theo tiền tố tại ranh giới segment, ưu tiên tiền tố DÀI nhất. Mọi thứ khác ⇒ không có route (404).
Gateway KHÔNG hardcode danh sách route: thêm route = sửa registry/route-map.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GATEWAY_TARGET = "gateway"
_DEFAULT_HOST = "http://127.0.0.1"


class RouteConfigError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    prefix: str
    target: str          # tên service trong registry hoặc "gateway"
    upstream: str | None  # khoá upstream (vd "identity") hoặc None khi gateway tự xử lý


def upstream_key(service_name: str) -> str:
    return service_name.removesuffix("-service")


class RouteTable:
    def __init__(self, routes: list[Route], upstream_urls: dict[str, str]) -> None:
        self._by_prefix: dict[str, Route] = {}
        for r in routes:
            existing = self._by_prefix.get(r.prefix)
            if existing and existing.target != r.target:
                raise RouteConfigError(f"tiền tố {r.prefix} trỏ tới hai đích: {existing.target} và {r.target}")
            self._by_prefix[r.prefix] = r
        self.upstream_urls = upstream_urls
        self.routes = sorted(self._by_prefix.values(), key=lambda r: r.prefix)
        for r in self.routes:
            if r.upstream and r.upstream not in upstream_urls:
                raise RouteConfigError(f"route {r.prefix}: chưa có URL upstream cho `{r.upstream}`")

    def resolve(self, path: str) -> Route | None:
        p = path.rstrip("/") or "/"
        while True:
            hit = self._by_prefix.get(p)
            if hit is not None:
                return hit
            cut = p.rfind("/")
            if cut <= 0:
                return None
            p = p[:cut]

    @property
    def upstream_names(self) -> list[str]:
        return sorted({r.upstream for r in self.routes if r.upstream})


def load_route_table(route_map_path: str | Path, registry_path: str | Path,
                     overrides: dict[str, str] | None = None) -> RouteTable:
    try:
        route_map = json.loads(Path(route_map_path).read_text(encoding="utf-8"))
        registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RouteConfigError(f"không đọc được route-map/registry: {type(exc).__name__}") from exc

    services = registry["services"]
    routes: list[Route] = []

    def add(prefix: str, target: str) -> None:
        if not prefix.startswith("/") or prefix.endswith("/") and prefix != "/":
            raise RouteConfigError(f"tiền tố không hợp lệ: {prefix!r}")
        if prefix.strip("/").split("/")[0].lower() == "internal":
            raise RouteConfigError("không được khai báo route /internal")
        if target == GATEWAY_TARGET:
            routes.append(Route(prefix, target, None))
        elif target in services:
            routes.append(Route(prefix, target, upstream_key(target)))
        else:
            raise RouteConfigError(f"đích không có trong registry: {target}")

    for rule in route_map["rules"]:
        add(rule["legacy_prefix"], rule["target"])
    for name, svc in services.items():
        for prefix in svc.get("public_prefixes") or []:
            add(prefix, name)

    urls = {upstream_key(n): f"{_DEFAULT_HOST}:{s['port']}" for n, s in services.items()
            if s.get("public_prefixes") or any(r.target == n for r in routes)}
    urls.update(overrides or {})
    return RouteTable(routes, urls)
