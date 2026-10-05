"""Người dùng đã xác thực, dựng từ claim của access token (docs/api/service-auth-contract-v1.md §2)."""

from __future__ import annotations

from dataclasses import dataclass, field

ADMIN_ROLES = frozenset({"admin", "dash_admin"})


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str
    role: str
    perms: frozenset[str] = frozenset()
    client_type: str = "web"
    sid: str = ""
    claims: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_admin(self) -> bool:
        """admin hoặc dash_admin: được xem/quản trị xuyên owner."""
        return self.role in ADMIN_ROLES

    def has_perm(self, permission: str) -> bool:
        """`role=admin` được coi là có mọi quyền quản trị (contract §2)."""
        return self.role == "admin" or permission in self.perms

    @classmethod
    def from_claims(cls, claims: dict) -> "Principal":
        return cls(sub=claims["sub"], username=claims.get("username", ""), role=claims.get("role", ""),
                   perms=frozenset(claims.get("perms") or ()), client_type=claims.get("client_type", "web"),
                   sid=claims.get("sid", ""), claims=claims)
