"""module: routing
service: api-gateway
phase: 5
status: active
responsibility: route theo infra/gateway/route-map.json, proxy streaming, không bao giờ route /internal
boundary: chỉ import từ chính service này và packages dùng chung (manager_observability, manager_contracts).
"""
