"""Credential resolution. One implementation, used by the service and the tools.

Credentials are referenced, never inlined. A reference names where the secret
lives; the value is fetched at the moment of use, is never logged, never written
to disk, never returned in an API response, and never reaches a prompt.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]

from app.core.errors import AutomationError, ErrorCode


class Credentials(dict):
    """A dict whose repr cannot leak the secret into a log line or a traceback."""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Credentials(username={self.get('username', '')!r}, password=***)"

    __str__ = __repr__


def resolve_secret(ref: str) -> Credentials:
    """Resolve `env://PREFIX` or `vault://kv/path` to {username, password}."""
    if not ref:
        raise AutomationError(ErrorCode.LOGIN_FAILED, "No credential reference configured.")

    if ref.startswith("env://"):
        prefix = ref.removeprefix("env://")
        creds = Credentials(username=os.environ.get(f"{prefix}_USERNAME", ""),
                            password=os.environ.get(f"{prefix}_PASSWORD", ""))
    elif ref.startswith("file://"):
        # A plain local file, e.g. login.txt next to the project:
        #     username=YOUR.USER
        #     password=your password
        # It never leaves this machine, is excluded from git and from the
        # release package, and is read only by the worker process.
        path = Path(ref.removeprefix("file://"))
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.exists():
            raise AutomationError(ErrorCode.LOGIN_FAILED,
                                  f"Credential file not found: {path.name}",
                                  details={"looked_in": str(path)})
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip().lower()] = value.strip().strip('"').strip("'")
        creds = Credentials(
            username=values.get("username") or values.get("user") or values.get("sis_username", ""),
            password=values.get("password") or values.get("pass") or values.get("sis_password", ""))
    elif ref.startswith("vault://"):
        try:
            import hvac
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise AutomationError(ErrorCode.LOGIN_FAILED,
                                  "Vault client is not installed.") from exc
        addr, token = os.environ.get("VAULT_ADDR"), os.environ.get("VAULT_TOKEN")
        if not addr or not token:
            raise AutomationError(ErrorCode.LOGIN_FAILED,
                                  "VAULT_ADDR / VAULT_TOKEN are not configured.")
        client = hvac.Client(url=addr, token=token)
        path = ref.removeprefix("vault://kv/")
        data: dict[str, Any] = client.secrets.kv.v2.read_secret_version(
            path=path)["data"]["data"]
        creds = Credentials(username=data.get("username", ""),
                            password=data.get("password", ""))
    else:
        raise AutomationError(ErrorCode.LOGIN_FAILED,
                              f"Unsupported secret reference scheme: {ref.split(':')[0]}")

    if not creds.get("username") or not creds.get("password"):
        raise AutomationError(
            ErrorCode.LOGIN_FAILED,
            f"Credentials are missing or incomplete at reference '{ref}'.",
            details={"reference": ref})   # the reference is safe to show; the value never is
    return creds
