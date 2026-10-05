"""
Roles and permissions — the one table every API route is checked against.

Enforced on the server, per request. The dashboard reads the same table
(via /api/auth/me) only to decide which controls to draw; hiding a button is
a courtesy, never the control.
"""

ADMIN, OPERATOR, VIEWER = "ADMIN", "OPERATOR", "VIEWER"
ROLES = (ADMIN, OPERATOR, VIEWER)

# What each permission lets a person do.
PERMISSIONS = {
    "dashboard.view":     "See the Control Tower: runs, shipments, ATLAS insights",
    "runs.view":          "See every run and its history",
    "runs.start":         "Start the automation",
    "runs.stop":          "Stop or pause a run",
    "human.act":          "Open & Continue a Human Action and use its browser session",
    "evidence.view":      "Open captured screenshots and evidence",
    "evidence.upload":    "Attach a screenshot to ATLAS chat",
    "atlas.chat":         "Ask ATLAS questions",
    "atlas.approve":      "Approve or reject an ATLAS proposal",
    "users.manage":       "Create users, change roles, activate or deactivate",
    "audit.view":         "Read the audit log",
    "settings.manage":    "Change system settings and register workers",
    "health.view":        "See worker heartbeat and system health",
}

ROLE_PERMISSIONS = {
    ADMIN: frozenset(PERMISSIONS),
    OPERATOR: frozenset((
        "dashboard.view", "runs.view", "runs.start", "runs.stop", "human.act",
        "evidence.view", "evidence.upload", "atlas.chat", "health.view")),
    VIEWER: frozenset((
        "dashboard.view", "runs.view", "atlas.chat", "health.view")),
}

ROLE_LABELS = {ADMIN: "Admin", OPERATOR: "Operator", VIEWER: "Viewer"}

# Entra ID app roles that map onto ours (when ENTRA_ROLE_CLAIMS is on).
ENTRA_APP_ROLES = {"ATA.Admin": ADMIN, "ATA.Operator": OPERATOR,
                   "ATA.Viewer": VIEWER}


def normal_role(role):
    role = str(role or "").strip().upper()
    return role if role in ROLES else None


def allowed(user, permission):
    """True when this signed-in, active user holds the permission."""
    if not user or not user.get("active"):
        return False
    if permission not in PERMISSIONS:
        raise KeyError("unknown permission: {0}".format(permission))
    return permission in ROLE_PERMISSIONS.get(user.get("role"), ())


def permissions_of(user):
    if not user or not user.get("active"):
        return []
    return sorted(ROLE_PERMISSIONS.get(user.get("role"), ()))


def matrix():
    """The RBAC matrix, for the docs and the Access page."""
    return [{"permission": p, "meaning": PERMISSIONS[p],
             "roles": [r for r in ROLES if p in ROLE_PERMISSIONS[r]]}
            for p in PERMISSIONS]
