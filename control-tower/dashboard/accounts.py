"""
Personal accounts for the office dashboard: a sign-in page and three access
levels (Viewer, Operator, Admin), instead of one shared access key.

The same accounts, sessions, roles and audit log the remote Control Tower uses
(controlplane/users.py, rbac.py, audit.py), kept in a small SQLite file on
this machine (dashboard/.runtime/accounts.db). Nothing new is invented here:
passwords are scrypt-hashed, a new person gets a one-time link (72 h) to set
their own, five wrong passwords lock an account for 15 minutes, sessions end
after 30 minutes idle or 12 hours, and every sign-in, role change and refused
request is in the audit log.

ON when DASHBOARD_ACCOUNTS=1, or (the default, "auto") as soon as an account
exists. The first Admin is created on this machine:

    python -m dashboard.accounts create-admin --email you@mantracghana.com --name "Your Name"
    (or double-click CREATE_ADMIN.bat)

It prints a one-time link to set the password. From then on everyone signs in
at http://<this machine>:8787/login and the access key no longer opens the
dashboard. DASHBOARD_ACCOUNTS=0 turns accounts off again (the access key
returns); the accounts file is kept.

Over plain http on the company network the session cookie cannot be marked
Secure; put the dashboard behind HTTPS (ATA_PUBLIC_URL=https://...) to have it.
"""

import argparse
import os
import re
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PUBLIC_ROUTES = ("/login", "/set-password", "/api/auth/login", "/api/auth/config",
                 "/api/auth/set-password", "/favicon.ico")
PUBLIC_PREFIXES = ("/static/brand/", "/static/login/")

# Which permission each office-dashboard route needs. GET routes not listed
# need dashboard.view; a POST or PATCH not listed is refused (no default allow).
GET_PERMISSIONS = (
    ("/api/export.csv", "runs.view"),
    ("/api/report.pdf", "runs.view"),
    ("/api/share", "users.manage"),
    ("/api/evidence", "evidence.view"),
    ("/api/ask/progress", "atlas.chat"),
    ("/api/po", "po.view"),
    ("/api/session/", "human.act"),
    ("/api/admin/users", "users.manage"),
    ("/api/admin/audit", "audit.view"),
    ("/api/admin/conversations", "users.manage"),
    ("/api/admin/rbac", "dashboard.view"),
    ("/clearance", "clearance.view"),
    ("/api/clearance", "clearance.view"),
    ("/api/knowledge/proposals", "knowledge.propose"),
    ("/api/knowledge/lessons", "runs.view"),
)
WRITE_PERMISSIONS = (
    ("/api/control", "runs.start"),
    ("/api/human", "human.act"),
    ("/api/session/", "human.act"),
    ("/api/evidence/upload", "evidence.upload"),
    ("/api/po", "po.view"),                 # po/web.py checks process/send/resend itself
    ("/api/feedback", "atlas.chat"),
    ("/api/ask", "atlas.chat"),
    ("/api/admin/users", "users.manage"),
    ("/api/me/prefs", "dashboard.view"),
    ("/api/auth/logout", "dashboard.view"),
    ("/api/clearance/upload", "clearance.upload"),
    ("/api/clearance/contacts", "clearance.contacts"),
    ("/api/clearance/escalate", "clearance.escalate"),
    ("/api/clearance/resolve", "clearance.escalate"),
    ("/api/clearance/contact-log", "clearance.view"),
    ("/api/knowledge/propose", "knowledge.propose"),
    ("/api/knowledge/review", "knowledge.review"),
    ("/api/knowledge/status", "knowledge.review"),
    ("/api/knowledge/ingest", "knowledge.review"),
)


def mode():
    """'on', 'off' or 'auto' (DASHBOARD_ACCOUNTS)."""
    raw = str(os.environ.get("DASHBOARD_ACCOUNTS") or "auto").strip().lower()
    if raw in ("1", "on", "true", "yes"):
        return "on"
    if raw in ("0", "off", "false", "no"):
        return "off"
    return "auto"


def db_path():
    return Path(os.environ.get("DASHBOARD_ACCOUNTS_DB") or
                (Path(os.environ.get("ATA_RUNTIME_DIR") or HERE / ".runtime") / "accounts.db"))


class Accounts(object):
    """The store, built on first use."""

    def __init__(self, path=None):
        from controlplane.audit import Audit
        from controlplane.config import Settings
        from controlplane.db import Database
        from controlplane.security import RateLimiter
        from controlplane.users import Users
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["DATABASE_URL"] = "sqlite:///" + str(self.path).replace("\\", "/")
        public = (env.get("ATA_PUBLIC_URL") or "").strip()
        if not public.startswith("https://"):
            env["ATA_INSECURE_COOKIES"] = "1"      # http on the company network
        env.setdefault("ATA_LOCAL_LOGIN", "1")
        self.settings = Settings(env)
        self.db = Database(self.settings.database_url)
        self.audit = Audit(self.db)
        self.users = Users(self.db, self.audit, self.settings)
        self.limiter = RateLimiter()

    def has_accounts(self):
        """Whether any account exists; read at most every few seconds."""
        import time
        hit = getattr(self, "_has", None)
        if hit and time.time() - hit[0] < 3:
            return hit[1]
        try:
            found = bool(self.db.one("SELECT user_id FROM users LIMIT 1", ()))
        except Exception:
            found = False
        self._has = (time.time(), found)
        return found


_lock = threading.Lock()
_store = {"obj": None, "checked": None}


def store():
    with _lock:
        if _store["obj"] is None:
            _store["obj"] = Accounts()
        return _store["obj"]


def enabled():
    """True when the dashboard requires a personal sign-in."""
    m = mode()
    if m == "off":
        return False
    if m == "on":
        return True
    if _store["obj"] is not None:
        return _store["obj"].has_accounts()
    if not db_path().exists():
        return False
    return store().has_accounts()


def reset_for_tests():
    with _lock:
        _store["obj"] = None


def permission_for(method, route):
    """The permission a route needs, or None when it is refused outright."""
    if method in ("GET", "HEAD"):
        for prefix, permission in GET_PERMISSIONS:
            if route == prefix or (prefix.endswith("/") and route.startswith(prefix)) or \
                    route.startswith(prefix + "/"):
                return permission
        return "dashboard.view"
    for prefix, permission in WRITE_PERMISSIONS:
        if route == prefix or (prefix.endswith("/") and route.startswith(prefix)) or \
                route.startswith(prefix + "/"):
            return permission
    return None


def link(base, token):
    return "{0}/set-password#token={1}".format(base.rstrip("/"), token) if token else None


# ── command line: the first Admin, and help when locked out ───────────────

def _base_url():
    host = os.environ.get("DASHBOARD_PUBLIC_HOST") or os.environ.get("COMPUTERNAME") or "localhost"
    port = os.environ.get("DASHBOARD_PORT") or "8787"
    return (os.environ.get("ATA_PUBLIC_URL") or "http://{0}:{1}".format(host.lower(), port)).rstrip("/")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m dashboard.accounts",
                                     description="Accounts for the office dashboard.")
    sub = parser.add_subparsers(dest="cmd")
    a = sub.add_parser("create-admin", help="create an Admin and print their set-password link")
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    r = sub.add_parser("reset", help="sign someone out everywhere and print a new link")
    r.add_argument("--email", required=True)
    sub.add_parser("list", help="list the accounts")
    args = parser.parse_args(argv)
    from controlplane.users import AccessError
    acc = store()
    if args.cmd == "create-admin":
        try:
            user, token = acc.users.create(args.email, args.name, "ADMIN")
        except AccessError as error:
            print("Not created: {0}".format(error))
            return 1
        print("\nAdmin created: {0} <{1}>".format(user["display_name"], user["work_email"]))
        print("Open this link once to set the password (valid 72 hours):\n")
        print("    " + link(_base_url(), token) + "\n")
        print("From now on the dashboard asks everyone to sign in at {0}/login.".format(_base_url()))
        print("Add the team in Settings -> Access levels.\n")
        return 0
    if args.cmd == "reset":
        user = acc.users.by_email(args.email)
        if not user:
            print("No account for {0}.".format(args.email))
            return 1
        token = acc.users.reset_access(user["user_id"], None)
        print("Signed out everywhere.")
        if token:
            print("New set-password link (72 hours):\n\n    " + link(_base_url(), token) + "\n")
        return 0
    if args.cmd == "list":
        for u in acc.users.list():
            print("{0:<32} {1:<9} {2}".format(u.get("work_email"), u.get("role"),
                                              "active" if u.get("active") else "inactive"))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
