"""
Control plane settings, from the environment only.

Secrets (the session key, the Entra client secret, database passwords) come
from the environment, which on Azure App Service is filled from Key Vault
references. Nothing secret is ever written into a page or an API response.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _on(raw):
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


class Settings(object):
    """Read once at start; tests build their own."""

    def __init__(self, env=None):
        env = os.environ if env is None else env
        get = env.get
        self.host = get("ATA_HOST", "127.0.0.1")
        # App Service tells the app which port to listen on in PORT.
        self.port = int(get("PORT") or get("ATA_PORT") or 8800)
        # The address people type. Used for the Entra redirect URI and for
        # the Origin check on every state-changing request.
        self.public_url = (get("ATA_PUBLIC_URL") or "").rstrip("/") or None
        self.database_url = get("DATABASE_URL") or "sqlite:///{0}".format(
            ROOT / "controlplane" / "data" / "ata.db")
        # Behind Azure App Service the TLS connection ends at the front end;
        # X-Forwarded-Proto/For are trusted only when this says so.
        self.trust_proxy = _on(get("ATA_TRUST_PROXY"))
        # Secure cookies need HTTPS. Off only for local development over
        # http://127.0.0.1, and refused when ATA_PUBLIC_URL is https.
        insecure = _on(get("ATA_INSECURE_COOKIES"))
        self.secure_cookies = not insecure or (
            self.public_url or "").startswith("https://")
        self.session_idle_s = int(get("ATA_SESSION_IDLE_MIN", 30)) * 60
        self.session_max_s = int(get("ATA_SESSION_MAX_HOURS", 12)) * 3600
        self.login_attempts = int(get("ATA_LOGIN_ATTEMPTS", 5))
        self.lockout_s = int(get("ATA_LOCKOUT_MIN", 15)) * 60
        self.allow_local_login = _on(get("ATA_LOCAL_LOGIN", "1"))
        # Microsoft Entra ID (OpenID Connect, authorization code + PKCE).
        self.entra_tenant = get("ENTRA_TENANT_ID") or None
        self.entra_client_id = get("ENTRA_CLIENT_ID") or None
        self.entra_client_secret = get("ENTRA_CLIENT_SECRET") or None
        self.entra_allowed_domains = [d.strip().lower() for d in (
            get("ENTRA_ALLOWED_DOMAINS") or "").split(",") if d.strip()]
        # A person who signs in with Entra but has no account here is let in
        # only when this is on, and then as a Viewer — an admin raises it.
        self.entra_auto_provision = _on(get("ENTRA_AUTO_PROVISION"))
        # Entra app roles (ATA.Admin / ATA.Operator / ATA.Viewer) decide the
        # role at each sign-in when this is on; otherwise the role is the
        # one an admin set here.
        self.entra_role_claims = _on(get("ENTRA_ROLE_CLAIMS"))
        self.entra_authority = (get("ENTRA_AUTHORITY") or
                                "https://login.microsoftonline.com").rstrip("/")
        # A worker that has not called in for this long is OFFLINE.
        self.worker_offline_s = int(get("ATA_WORKER_OFFLINE_S", 30))
        self.allow_concurrent_runs = _on(get("ATA_ALLOW_CONCURRENT_RUNS"))
        # The remote browser view: how long a claim lasts without a sign of
        # life from the person's tab.
        self.session_lease_s = int(get("ATA_SESSION_LEASE_S", 45))
        self.static_dir = ROOT / "dashboard" / "static"
        self.intel_dir = get("ATLAS_INTEL_DIR") or None
        # PO Automation: jobs, events, the send ledger and generated output.
        self.po_dir = get("PO_DATA_DIR") or str(ROOT / "controlplane" / "data" / "po")

    @property
    def entra_enabled(self):
        return bool(self.entra_tenant and self.entra_client_id and
                    self.entra_client_secret and self.public_url)

    @property
    def redirect_uri(self):
        return (self.public_url or "") + "/auth/sso/callback"
