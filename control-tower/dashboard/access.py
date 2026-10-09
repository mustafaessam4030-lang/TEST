"""
The dashboard's access key — never hardcoded, never shipped.

Where the key comes from, first match wins:

    1. an explicit key (the --key option of the launchers)
    2. the DASHBOARD_ACCESS_KEY environment variable
    3. the local key file (DASHBOARD_ACCESS_KEY_FILE, default
       dashboard/.runtime/access_key) — created on first use with a random
       key, readable by this user only

dashboard/.runtime/ is runtime state: it is excluded from Git and from the
release package, so each installation has its own key. Rotate the key by
deleting the file (or setting the variable) and restarting.

The key is printed in the dashboard links on the console of the machine that
starts the dashboard, exactly as before; it is never written to the run log.
"""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_LENGTH = 12


def key_file():
    return Path(os.environ.get("DASHBOARD_ACCESS_KEY_FILE") or
                (Path(os.environ.get("ATA_RUNTIME_DIR") or ROOT / ".runtime") / "access_key"))


def _usable(value):
    value = (value or "").strip()
    return value if len(value) >= MIN_LENGTH else None


def resolve(explicit=None, generate=True):
    """(key, source). source: 'option' | 'environment' | 'file' | 'generated' | None.

    A configured key shorter than 12 characters is refused (ValueError) rather
    than silently replaced: the operator asked for it, so they must fix it.
    """
    for value, source in ((explicit, "option"),
                          (os.environ.get("DASHBOARD_ACCESS_KEY"), "environment")):
        if value not in (None, ""):
            if not _usable(value):
                raise ValueError("the dashboard access key from the {0} is shorter than {1} "
                                 "characters".format(source, MIN_LENGTH))
            return value.strip(), source
    path = key_file()
    try:
        stored = _usable(path.read_text(encoding="utf-8"))
        if stored:
            return stored, "file"
    except OSError:
        pass
    if not generate:
        return None, None
    key = secrets.token_urlsafe(18)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created it first: use theirs.
        stored = _usable(path.read_text(encoding="utf-8"))
        if stored:
            return stored, "file"
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    return key, "generated"


def explain(source):
    """One console line on where the key came from (never the key itself)."""
    return {
        "option": "Access key: from the --key option.",
        "environment": "Access key: from the DASHBOARD_ACCESS_KEY environment variable.",
        "file": "Access key: this installation's key in {0}.".format(key_file()),
        "generated": "Access key: a new random key was generated for this installation and "
                     "saved to {0} (never in Git or the release). Set DASHBOARD_ACCESS_KEY "
                     "to choose your own; delete the file to rotate it.".format(key_file()),
    }.get(source, "")
