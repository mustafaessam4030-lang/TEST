"""Bearer tokens for the Snowflake REST API (Cortex Agents).

Two documented options, both `Authorization: Bearer …`:

  * programmatic access token (PAT)       X-Snowflake-Authorization-Token-Type: PROGRAMMATIC_ACCESS_TOKEN
  * key-pair JWT, signed with the user's  X-Snowflake-Authorization-Token-Type: KEYPAIR_JWT
    private key: iss = ACCOUNT.USER.SHA256:<fingerprint>, sub = ACCOUNT.USER, ≤ 1 h

The token is built on demand, lives only in the request headers, and is never
logged. Its repr is masked.
"""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from typing import Any

from app.cortex.errors import unavailable


class BearerHeaders(dict):
    def __repr__(self) -> str:
        return "BearerHeaders(Authorization=***)"

    __str__ = __repr__


def _jwt(cfg: Any) -> str:
    try:
        import jwt  # PyJWT — installed with snowflake-connector-python
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:
        raise unavailable("driver_missing", "PyJWT/cryptography not installed") from exc

    key_path = Path(cfg._key_path())
    passphrase = cfg.private_key_passphrase.encode() if cfg.private_key_passphrase else None
    try:
        private_key = serialization.load_pem_private_key(key_path.read_bytes(),
                                                         password=passphrase)
    except (OSError, ValueError, TypeError) as exc:
        raise unavailable("agent_auth", f"private key could not be read: {type(exc).__name__}"
                          ) from exc
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(public_der).digest()).decode()
    account, user = cfg.jwt_account, str(cfg.user or "").upper()
    now = int(time.time())
    payload = {"iss": f"{account}.{user}.{fingerprint}", "sub": f"{account}.{user}",
               "iat": now, "exp": now + 3000}
    return jwt.encode(payload, private_key, algorithm="RS256")


def bearer_headers(cfg: Any) -> BearerHeaders:
    method = cfg.rest_auth_method
    if method == "pat":
        return BearerHeaders({"Authorization": f"Bearer {cfg.pat}",
                              "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN"})
    if method == "keypair":
        return BearerHeaders({"Authorization": f"Bearer {_jwt(cfg)}",
                              "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT"})
    raise unavailable("agent_auth", f"sign-in method is {cfg.auth_method}")
