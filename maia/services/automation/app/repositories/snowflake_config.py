"""Where the Snowflake connection comes from — and where its secrets do NOT go.

Three places can describe the connection, in this order of precedence:

  1. `MAIA_SNOWFLAKE_*` environment variables (explicitly set ones only)
  2. a local `snowflake.txt` next to the project, same idea as `login.txt`:

        account=xy12345.west-europe.azure
        user=MAIA_SVC
        authenticator=externalbrowser     # or: password / keypair
        role=MAIA_APP
        warehouse=MAIA_WH
        database=MAIA_PROD
        schema=CORE

  3. the defaults in `app.config.Settings`

Three ways to sign in, chosen by what is present:

  * `authenticator=externalbrowser` — company SSO in a browser window, nothing
    stored here at all. The token is cached by the driver between runs.
  * `private_key_file=` (+ optional `private_key_passphrase=`) — key pair, the
    right choice for a service account.
  * `password=` — also takes a Snowflake programmatic access token (PAT).

Secrets are read at the moment of connecting and live only in the kwargs the
driver receives. `SnowflakeSettings.summary()` is the only thing any script,
log or API may print, and it never contains one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]

# Keys a snowflake.txt may hold, and the aliases people actually type.
_ALIASES = {
    "account": "account", "account_identifier": "account", "accountname": "account",
    "user": "user", "username": "user", "login": "user",
    "authenticator": "authenticator", "auth": "authenticator",
    "password": "password", "pat": "password", "token": "password",
    "private_key_file": "private_key_file", "private_key": "private_key_file",
    "key_file": "private_key_file",
    "private_key_passphrase": "private_key_passphrase", "passphrase": "private_key_passphrase",
    "private_key_file_pwd": "private_key_passphrase",
    "role": "role", "warehouse": "warehouse", "database": "database", "schema": "schema",
    "host": "host",
}
SECRET_KEYS = ("password", "private_key_passphrase")


class ConnectKwargs(dict):
    """The driver's kwargs. Its repr cannot put a secret in a log or traceback."""

    def __repr__(self) -> str:
        shown = {k: ("***" if k in ("password", "private_key_file_pwd", "token") else v)
                 for k, v in self.items()}
        return f"ConnectKwargs({shown!r})"

    __str__ = __repr__


@dataclass
class SnowflakeSettings:
    account: str | None = None
    user: str | None = None
    authenticator: str | None = None
    password: str | None = field(default=None, repr=False)
    private_key_file: str | None = None
    private_key_passphrase: str | None = field(default=None, repr=False)
    role: str | None = None
    warehouse: str | None = None
    database: str | None = None
    schema: str | None = None
    host: str | None = None
    #: which file, if any, contributed — its NAME, safe to print
    config_file: str | None = None

    # ── what kind of sign-in this is ────────────────────────────────────────
    @property
    def auth_method(self) -> str:
        auth = (self.authenticator or "").strip().lower()
        if auth == "externalbrowser" or auth == "sso":
            return "sso"
        if auth.startswith("https://"):          # Okta native URL
            return "okta"
        if self.private_key_file or auth in ("keypair", "snowflake_jwt"):
            return "keypair"
        if self.password:
            return "password"
        return "none"

    def problems(self) -> list[str]:
        """What stops a connection attempt, in words an operator can act on."""
        out = []
        example = [k for k in ("account", "user", "password")
                   if "YOUR_" in str(getattr(self, k) or "").upper()]
        if example:
            out.append(f"{', '.join(example)} still has the example text — "
                       "put your real value in snowflake.txt")
        if not self.account:
            out.append("account is not set (e.g. account=xy12345.west-europe.azure)")
        if not self.user:
            out.append("user is not set")
        method = self.auth_method
        if method == "none":
            out.append("no way to sign in: set authenticator=externalbrowser, "
                       "private_key_file=…, or password=…")
        if method == "keypair":
            if not self.private_key_file:
                out.append("authenticator=keypair needs private_key_file=")
            elif not self._key_path().exists():
                out.append(f"private key file not found: {self._key_path().name}")
        if method == "okta" and not self.password:
            out.append("Okta sign-in needs password=")
        if not self.database or not self.schema:
            out.append("database and schema must both be set")
        if not self.warehouse:
            out.append("warehouse is not set — queries cannot run without one")
        return out

    def _key_path(self) -> Path:
        path = Path(str(self.private_key_file).removeprefix("file://"))
        return path if path.is_absolute() else REPO_ROOT / path

    def connect_kwargs(self) -> ConnectKwargs:
        kw = ConnectKwargs(
            account=self.account, user=self.user, role=self.role,
            warehouse=self.warehouse, database=self.database, schema=self.schema,
            # A lookup runs for 40 seconds and the service for days: keep the
            # session alive rather than failing the first write after lunch.
            client_session_keep_alive=True,
            login_timeout=60, network_timeout=120,
            session_parameters={"QUERY_TAG": "maia-equipment", "TIMEZONE": "UTC"},
        )
        if self.host:
            kw["host"] = self.host
        method = self.auth_method
        if method == "sso":
            kw["authenticator"] = "externalbrowser"
            # Sign in once; later starts reuse the cached SSO token.
            kw["client_store_temporary_credential"] = True
        elif method == "okta":
            kw["authenticator"] = self.authenticator
            kw["password"] = self.password
        elif method == "keypair":
            kw["private_key_file"] = str(self._key_path())
            if self.private_key_passphrase:
                kw["private_key_file_pwd"] = self.private_key_passphrase
        elif method == "password":
            kw["password"] = self.password
        return ConnectKwargs({k: v for k, v in kw.items() if v not in (None, "")})

    def secrets(self) -> tuple[str, ...]:
        """Values to mask in anything written — error text included."""
        return tuple(v for v in (self.password, self.private_key_passphrase) if v)

    def summary(self) -> dict[str, Any]:
        """Everything safe to show about this connection. No secret, ever."""
        return {"account": self.account, "user": self.user, "auth": self.auth_method,
                "role": self.role, "warehouse": self.warehouse,
                "database": self.database, "schema": self.schema,
                "config_file": self.config_file}


def find_config_file(name: str | None = None) -> Path | None:
    """`snowflake.txt` — or `snowflake.txt.txt`, which is what Windows makes of it
    when extensions are hidden. The example file is never read as config."""
    if name:
        path = Path(name)
        path = path if path.is_absolute() else REPO_ROOT / path
        if path.exists():
            return path
    for candidate in sorted(REPO_ROOT.glob("snowflake*")):
        low = candidate.name.lower()
        if candidate.is_file() and "example" not in low and low.startswith("snowflake."):
            if low.endswith((".txt", ".cfg", ".ini", ".conf")) or low == "snowflake.txt":
                return candidate
    return None


def read_config_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        canon = _ALIASES.get(key.strip().lower().replace("-", "_"))
        if canon and value:
            values[canon] = value
    return values


def _resolve_ref(ref: str | None) -> str | None:
    """`env://VAR` or `file://path` → the value. Anything else is taken as-is
    only if it does not look like a reference at all."""
    if not ref:
        return None
    if ref.startswith("env://"):
        return os.environ.get(ref.removeprefix("env://")) or None
    if ref.startswith("file://"):
        path = Path(ref.removeprefix("file://"))
        path = path if path.is_absolute() else REPO_ROOT / path
        return path.read_text(encoding="utf-8").strip() if path.exists() else None
    return None


def load(settings: Any) -> SnowflakeSettings:
    """Merge environment, snowflake.txt and defaults into one description."""
    explicit = getattr(settings, "model_fields_set", set())
    path = find_config_file(getattr(settings, "snowflake_config_file", None))
    file_values = read_config_file(path) if path else {}

    def pick(key: str) -> str | None:
        attr = f"snowflake_{key}"
        if attr in explicit and getattr(settings, attr, None):
            return getattr(settings, attr)
        if file_values.get(key):
            return file_values[key]
        return getattr(settings, attr, None)

    # MAIA_SNOWFLAKE_PRIVATE_KEY_REF=file://… when set explicitly, else the file.
    key_ref = getattr(settings, "snowflake_private_key_ref", None) or ""
    private_key_file = file_values.get("private_key_file")
    if key_ref.startswith("file://") and ("snowflake_private_key_ref" in explicit
                                          or not private_key_file):
        private_key_file = key_ref.removeprefix("file://")

    password = (_resolve_ref(getattr(settings, "snowflake_password_ref", None))
                or file_values.get("password"))
    passphrase = (_resolve_ref(getattr(settings, "snowflake_private_key_passphrase_ref", None))
                  or file_values.get("private_key_passphrase"))

    return SnowflakeSettings(
        account=pick("account"), user=pick("user"),
        authenticator=pick("authenticator"),
        password=password, private_key_file=private_key_file,
        private_key_passphrase=passphrase,
        role=pick("role"), warehouse=pick("warehouse"),
        database=pick("database"), schema=pick("schema"),
        host=file_values.get("host"),
        config_file=path.name if path else None,
    )
