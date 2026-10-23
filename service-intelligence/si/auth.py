"""Application authentication and authorisation (server-side).

Modes (SI_AUTH_MODE):
  token          — `Authorization: Bearer <token>`; tokens are stored only as SHA-256 hashes in the users file.
  trusted_header — identity and groups from headers set by the company's authenticating reverse proxy (SSO).
                   Use only when the proxy strips those headers from incoming requests.
  dev            — a single development user; refused unless SI_ENV=dev.

Permissions gate each API route. A user's `scope` (for example {region: [North]})
is added as a mandatory filter to every query that user triggers, on top of
whatever the Snowflake role itself allows.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import contract

PERMISSIONS = ("analysis.read", "analysis.drilldown", "chat.ask", "plan.read", "plan.add", "plan.review",
               "plan.approve", "admin.read")
USER_ID = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    scope: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "roles": list(self.roles), "permissions": sorted(self.permissions),
                "scope": {k: list(v) for k, v in self.scope.items()}}


class Directory:
    def __init__(self, path: Path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        self.roles: dict[str, frozenset[str]] = {}
        for role, perms in (raw.get("roles") or {}).items():
            unknown = set(perms) - set(PERMISSIONS)
            if unknown:
                raise ValueError(f"Unknown permissions for role {role}: {sorted(unknown)}")
            self.roles[role] = frozenset(perms)
        self.group_roles = {str(g): str(r) for g, r in (raw.get("group_roles") or {}).items()}
        self.users: dict[str, dict] = {}
        self.by_token: dict[str, str] = {}
        for u in raw.get("users") or []:
            uid = str(u["id"])
            if not USER_ID.match(uid):
                raise ValueError(f"Bad user id {uid!r}")
            scope = {}
            for dim, vals in (u.get("scope") or {}).items():
                if dim not in contract.DIMENSIONS:
                    raise ValueError(f"Scope dimension {dim} is not a contract dimension")
                scope[dim] = tuple(str(v) for v in vals)
            for r in u.get("roles") or []:
                if r not in self.roles:
                    raise ValueError(f"User {uid} has unknown role {r}")
            self.users[uid] = {"id": uid, "name": u.get("name", uid), "roles": tuple(u.get("roles") or ()), "scope": scope}
            if u.get("token_sha256"):
                self.by_token[str(u["token_sha256"]).lower()] = uid

    def principal(self, uid: str, extra_roles: tuple[str, ...] = ()) -> Principal:
        u = self.users.get(uid, {"id": uid, "name": uid, "roles": (), "scope": {}})
        roles = tuple(dict.fromkeys(u["roles"] + extra_roles))
        perms = frozenset().union(*(self.roles.get(r, frozenset()) for r in roles)) if roles else frozenset()
        return Principal(uid, u["name"], roles, perms, dict(u["scope"]))

    def from_token(self, token: str) -> Principal | None:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for known, uid in self.by_token.items():
            if hmac.compare_digest(known, digest):
                return self.principal(uid)
        return None


def authenticate(mode: str, directory: Directory, headers, user_header: str, groups_header: str) -> Principal:
    if mode == "dev":
        uid = next(iter(directory.users), None)
        if uid is None:
            raise AuthError(500, "No development user is configured")
        return directory.principal(uid)
    if mode == "token":
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or len(auth) > 600:
            raise AuthError(401, "Sign-in required")
        p = directory.from_token(auth[7:].strip())
        if p is None:
            raise AuthError(401, "Sign-in required")
        return p
    if mode == "trusted_header":
        uid = (headers.get(user_header) or "").strip()
        if not USER_ID.match(uid):
            raise AuthError(401, "Sign-in required")
        groups = [g.strip() for g in (headers.get(groups_header) or "").split(",") if g.strip()]
        extra = tuple(directory.group_roles[g] for g in groups if g in directory.group_roles)
        return directory.principal(uid, extra)
    raise AuthError(500, "Authentication is not configured")
