"""
Accounts and sessions.

Every person has their own account; there is no shared password. A local
account starts with no password at all: the admin who creates it gets a
one-time link (valid 72 hours) to hand to that person, who sets their own.
An Entra ID account never has a local password.

Sessions are server-side. The browser holds only a random token in an
HttpOnly, Secure, SameSite cookie; the database holds its SHA-256. A session
ends at logout, after ATA_SESSION_IDLE_MIN without a request, after
ATA_SESSION_MAX_HOURS in all, or the moment the account is deactivated, its
role changes or an admin revokes its access.
"""

import re
import uuid

from . import rbac
from .db import dumps, loads, now
from .security import (hash_password, verify_password, password_problem,
                       new_token, token_hash)

EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
INVITE_S = 72 * 3600
THEMES = ("light", "dark", "system")
PUBLIC_FIELDS = ("user_id", "work_email", "display_name", "role", "active",
                 "auth_source", "created_at", "created_by", "updated_at",
                 "updated_by", "last_login", "must_set_password")


class AccessError(Exception):
    """A refused account change, with the reason a person can act on."""


def public(user):
    if not user:
        return None
    out = {k: user.get(k) for k in PUBLIC_FIELDS}
    out["active"] = bool(out["active"])
    out["must_set_password"] = bool(out["must_set_password"])
    out["role_label"] = rbac.ROLE_LABELS.get(user.get("role"), user.get("role"))
    out["prefs"] = loads(user.get("prefs"))
    out["locked"] = bool(user.get("locked_until") and user["locked_until"] > now())
    return out


class Users(object):
    def __init__(self, db, audit, settings):
        self.db, self.audit, self.settings = db, audit, settings

    # -- lookups -----------------------------------------------------------

    def get(self, user_id, conn=None):
        return self.db.one("SELECT * FROM users WHERE user_id = ?", (user_id,), conn)

    def by_email(self, email, conn=None):
        return self.db.one("SELECT * FROM users WHERE work_email = ?",
                           (str(email or "").strip().lower(),), conn)

    def list(self):
        return [public(u) for u in self.db.all(
            "SELECT * FROM users ORDER BY active DESC, display_name")]

    def count_admins(self, conn=None):
        row = self.db.one("SELECT COUNT(*) AS n FROM users WHERE role = ? AND active = 1",
                          (rbac.ADMIN,), conn)
        return int(row["n"]) if row else 0

    # -- creating ----------------------------------------------------------

    def create(self, email, display_name, role, by=None, password=None,
               auth_source="local", entra_oid=None, ip=None):
        """Returns (user, invite_token or None). Raises AccessError."""
        email = str(email or "").strip().lower()
        display_name = re.sub(r"\s+", " ", str(display_name or "")).strip()[:80]
        role = rbac.normal_role(role)
        if not EMAIL.match(email) or len(email) > 254:
            raise AccessError("Enter a valid work email address.")
        if not display_name:
            raise AccessError("Enter the person's name.")
        if role is None:
            raise AccessError("Choose a role: Admin, Operator or Viewer.")
        if auth_source not in ("local", "entra"):
            raise AccessError("Unknown sign-in method.")
        if password is not None:
            problem = password_problem(password, email)
            if problem:
                raise AccessError(problem)
        if self.by_email(email):
            raise AccessError("An account for {0} already exists.".format(email))
        user_id = "u_" + uuid.uuid4().hex[:16]
        stamp = now()
        self.db.execute(
            "INSERT INTO users (user_id, work_email, display_name, role, active, "
            "password_hash, auth_source, entra_oid, must_set_password, created_at, "
            "created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, email, display_name, role,
             hash_password(password) if password else None, auth_source, entra_oid,
             1 if (auth_source == "local" and not password) else 0, stamp,
             (by or {}).get("user_id"), stamp, (by or {}).get("user_id")))
        invite = None
        if auth_source == "local" and not password:
            invite = self._issue(user_id, "set_password", INVITE_S, by)
        user = self.get(user_id)
        self.audit.record("CREATE_USER", user=by, target_type="user",
                          target_id=user_id, ip=ip,
                          metadata={"email": email, "role": role,
                                    "auth_source": auth_source,
                                    "invite_link_issued": bool(invite)})
        return user, invite

    def _issue(self, user_id, purpose, ttl_s, by=None):
        token = new_token()
        stamp = now()
        self.db.execute("UPDATE tokens SET used_at = ? WHERE user_id = ? AND purpose = ? "
                        "AND used_at IS NULL", (stamp, user_id, purpose))
        self.db.execute(
            "INSERT INTO tokens (token_hash, user_id, purpose, created_at, created_by, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token_hash(token), user_id, purpose, stamp, (by or {}).get("user_id"),
             stamp + ttl_s))
        return token

    def token_user(self, token, purpose="set_password"):
        row = self.db.one("SELECT * FROM tokens WHERE token_hash = ? AND purpose = ?",
                          (token_hash(token), purpose))
        if not row or row["used_at"] or row["expires_at"] < now():
            return None
        user = self.get(row["user_id"])
        return user if user and user["active"] else None

    def set_password(self, token, password, ip=None):
        """The person's own password, from their one-time link."""
        user = self.token_user(token)
        if user is None:
            raise AccessError("This link has expired or was already used. Ask an "
                              "admin for a new one.")
        if user["auth_source"] != "local":
            raise AccessError("This account signs in with Microsoft.")
        problem = password_problem(password, user["work_email"])
        if problem:
            raise AccessError(problem)
        stamp = now()
        with self.db.tx() as c:
            changed = self.db.execute(
                "UPDATE tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (stamp, token_hash(token)), c)
            if changed != 1:
                raise AccessError("This link was already used.")
            self.db.execute(
                "UPDATE users SET password_hash = ?, must_set_password = 0, "
                "failed_logins = 0, locked_until = NULL, updated_at = ?, "
                "updated_by = ? WHERE user_id = ?",
                (hash_password(password), stamp, user["user_id"], user["user_id"]), c)
        self.revoke_sessions(user["user_id"])
        self.audit.record("SET_PASSWORD", user=user, target_type="user",
                          target_id=user["user_id"], ip=ip)
        return self.get(user["user_id"])

    # -- signing in ----------------------------------------------------------

    def authenticate(self, email, password, ip=None):
        """
        (user, None) or (None, reason-for-the-log). The person is told only
        "email or password is not right" whatever the reason, so the page
        does not reveal which accounts exist.
        """
        user = self.by_email(email)
        stored = user["password_hash"] if user and user["auth_source"] == "local" else None
        ok = verify_password(str(password or ""), stored)
        if user is None:
            return None, "no such account"
        if user["auth_source"] != "local":
            return None, "account signs in with Microsoft"
        if not user["active"]:
            return None, "account deactivated"
        if user["locked_until"] and user["locked_until"] > now():
            return None, "account locked after repeated failures"
        if not stored:
            return None, "password not set yet"
        if not ok:
            failures = int(user["failed_logins"] or 0) + 1
            locked = now() + self.settings.lockout_s \
                if failures >= self.settings.login_attempts else None
            self.db.execute("UPDATE users SET failed_logins = ?, locked_until = ? "
                            "WHERE user_id = ?",
                            (0 if locked else failures, locked, user["user_id"]))
            return None, ("wrong password; locked for {0} min".format(
                self.settings.lockout_s // 60) if locked else "wrong password")
        self.db.execute("UPDATE users SET failed_logins = 0, locked_until = NULL "
                        "WHERE user_id = ?", (user["user_id"],))
        return user, None

    def mark_login(self, user_id):
        self.db.execute("UPDATE users SET last_login = ? WHERE user_id = ?",
                        (now(), user_id))

    # -- sessions ------------------------------------------------------------

    def start_session(self, user, ip=None, user_agent=None, method="password"):
        token, csrf = new_token(), new_token(24)
        stamp = now()
        self.db.execute(
            "INSERT INTO sessions (session_hash, user_id, csrf, created_at, last_seen, "
            "expires_at, ip, user_agent, auth_method) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (token_hash(token), user["user_id"], csrf, stamp, stamp,
             stamp + self.settings.session_max_s, (ip or "")[:64],
             (user_agent or "")[:200], method))
        self.mark_login(user["user_id"])
        return token, csrf

    def resolve(self, token):
        """
        (user, session, None) for a live session, or (None, None, reason)
        where reason is "expired" when it timed out, so the page can say so.
        """
        if not token:
            return None, None, None
        session = self.db.one("SELECT * FROM sessions WHERE session_hash = ?",
                              (token_hash(token),))
        if session is None or session["revoked"]:
            return None, None, None
        stamp = now()
        if session["expires_at"] < stamp or \
                session["last_seen"] + self.settings.session_idle_s < stamp:
            self.db.execute("UPDATE sessions SET revoked = 1 WHERE session_hash = ?",
                            (session["session_hash"],))
            return None, None, "expired"
        user = self.get(session["user_id"])
        if user is None or not user["active"]:
            return None, None, None
        if stamp - session["last_seen"] > 30:
            self.db.execute("UPDATE sessions SET last_seen = ? WHERE session_hash = ?",
                            (stamp, session["session_hash"]))
        return user, session, None

    def end_session(self, token):
        self.db.execute("UPDATE sessions SET revoked = 1 WHERE session_hash = ?",
                        (token_hash(token),))

    def revoke_sessions(self, user_id):
        return self.db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ? "
                               "AND revoked = 0", (user_id,))

    # -- administration ------------------------------------------------------

    def update(self, user_id, by, role=None, active=None, display_name=None, ip=None):
        """An admin's change to one account. Every change is audited."""
        if by and by.get("user_id") == user_id and (
                (active is not None and not active) or
                (role is not None and rbac.normal_role(role) != by.get("role"))):
            raise AccessError("You cannot deactivate your own account or change "
                              "your own role. Ask another admin.")
        with self.db.tx() as c:
            user = self.db.one("SELECT * FROM users WHERE user_id = ?" +
                               self.db.lock_clause, (user_id,), c)
            if user is None:
                raise AccessError("No such user.")
            changes = {}
            if role is not None:
                new_role = rbac.normal_role(role)
                if new_role is None:
                    raise AccessError("Choose a role: Admin, Operator or Viewer.")
                if new_role != user["role"]:
                    changes["role"] = new_role
            if active is not None and bool(active) != bool(user["active"]):
                changes["active"] = 1 if active else 0
            if display_name is not None:
                name = re.sub(r"\s+", " ", str(display_name)).strip()[:80]
                if not name:
                    raise AccessError("Enter the person's name.")
                if name != user["display_name"]:
                    changes["display_name"] = name
            if not changes:
                return user
            losing_admin = user["role"] == rbac.ADMIN and user["active"] and (
                changes.get("role", rbac.ADMIN) != rbac.ADMIN or
                changes.get("active", 1) == 0)
            if losing_admin and self.count_admins(c) <= 1:
                raise AccessError("This is the last active admin. Make someone "
                                  "else an admin first.")
            sets = ", ".join("{0} = ?".format(k) for k in changes)
            self.db.execute(
                "UPDATE users SET {0}, updated_at = ?, updated_by = ? WHERE user_id = ?"
                .format(sets), list(changes.values()) +
                [now(), (by or {}).get("user_id"), user_id], c)
        if "role" in changes:
            self.revoke_sessions(user_id)
            self.audit.record("CHANGE_ROLE", user=by, target_type="user",
                              target_id=user_id, ip=ip, metadata={
                                  "email": user["work_email"], "from": user["role"],
                                  "to": changes["role"]})
        if "active" in changes:
            if not changes["active"]:
                self.revoke_sessions(user_id)
            self.audit.record("ENABLE_USER" if changes["active"] else "DISABLE_USER",
                              user=by, target_type="user", target_id=user_id, ip=ip,
                              metadata={"email": user["work_email"]})
        if "display_name" in changes:
            self.audit.record("SYSTEM_SETTING_CHANGED", user=by, target_type="user",
                              target_id=user_id, ip=ip,
                              metadata={"field": "display_name",
                                        "email": user["work_email"]})
        return self.get(user_id)

    def reset_access(self, user_id, by, ip=None):
        """End every session and, for a local account, issue a new one-time link."""
        user = self.get(user_id)
        if user is None:
            raise AccessError("No such user.")
        revoked = self.revoke_sessions(user_id)
        invite = None
        if user["auth_source"] == "local":
            self.db.execute("UPDATE users SET password_hash = NULL, must_set_password = 1, "
                            "failed_logins = 0, locked_until = NULL, updated_at = ?, "
                            "updated_by = ? WHERE user_id = ?",
                            (now(), (by or {}).get("user_id"), user_id))
            invite = self._issue(user_id, "set_password", INVITE_S, by)
        self.audit.record("RESET_ACCESS", user=by, target_type="user", target_id=user_id,
                          ip=ip, metadata={"email": user["work_email"],
                                           "sessions_ended": revoked,
                                           "invite_link_issued": bool(invite)})
        return invite

    # -- preferences ---------------------------------------------------------

    def set_prefs(self, user_id, prefs):
        user = self.get(user_id)
        current = loads(user["prefs"]) if user else {}
        if "theme" in prefs:
            if prefs["theme"] not in THEMES:
                raise AccessError("Theme is light, dark or system.")
            current["theme"] = prefs["theme"]
        self.db.execute("UPDATE users SET prefs = ? WHERE user_id = ?",
                        (dumps(current), user_id))
        return current
