"""
Where ATLAS keeps what it learns, and how a line gets there.

One folder (ATLAS_INTEL_DIR, default ml/data/intelligence), append-only JSON
lines, one lock per file. A write that fails is noted and dropped: a full
disk must never take a run down. Every string is redacted on the way in, and
the field names that could carry a secret are refused outright.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE.parent / "ml" / "data" / "intelligence"
MAX_READ_BYTES = 24 * 1024 * 1024

# Never written, whatever a caller passes.
FORBIDDEN_KEYS = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|"
    r"credential|security_code|captcha|otp|answer_code)", re.I)
_SECRETS = [
    (re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 [redacted]"),
    (re.compile(r"\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)"
                r"\s*[:=]\s*\S+", re.I), r"\1: [redacted]"),
    (re.compile(r"([?&](?:token|key|sig|signature|auth|session)[^=]*=)[^&\s]+", re.I),
     r"\1[redacted]"),
]
_locks = {}
_guard = threading.Lock()
_noted = set()

# REAL PRODUCTION DATA or TEST / DEMO DATA — decided per store folder, once,
# by the first process to write to it, and written down in ORIGIN. A process
# whose own origin differs is refused: test data can never be appended to a
# production store, nor production data to a test one. A process is
# "production" only when its entry point says so (the automation, the
# supervisor, the control plane set ATLAS_DATA_ORIGIN=production); anything
# else — tests, demos, tools — is "test".
ORIGINS = ("production", "test")
ORIGIN_FILE = "ORIGIN"


def origin():
    """This process's origin."""
    raw = str(os.environ.get("ATLAS_DATA_ORIGIN") or "").strip().lower()
    return raw if raw in ORIGINS else "test"


def store_origin():
    """The store's origin, or None while it has never been written to."""
    try:
        text = (folder() / ORIGIN_FILE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    return text if text in ORIGINS else None


def _admitted():
    marker = store_origin()
    mine = origin()
    if marker is None:
        try:
            with open(folder() / ORIGIN_FILE, "x", encoding="utf-8") as handle:
                handle.write(mine + "\n")
        except FileExistsError:
            return store_origin() == mine
        except OSError:
            return False
        return True
    return marker == mine


def folder():
    path = Path(os.environ.get("ATLAS_INTEL_DIR") or DEFAULT_DIR)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def redact(text, limit=400):
    if text is None:
        return None
    text = str(text)
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def clean(value, depth=0):
    """A JSON-safe, redacted copy. Forbidden keys are dropped, not masked."""
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {str(k): clean(v, depth + 1) for k, v in value.items()
                if not FORBIDDEN_KEYS.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [clean(v, depth + 1) for v in list(value)[:200]]
    if isinstance(value, str):
        return redact(value, 600)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(value, 200)


def _lock(path):
    with _guard:
        return _locks.setdefault(str(path), threading.Lock())


def append(name, record):
    """Append one record to <folder>/<name>. Returns True when written."""
    path = folder() / name
    if not _admitted():
        key = "origin:" + name
        if key not in _noted:
            _noted.add(key)
            try:
                import sys
                sys.stderr.write("ATLAS intelligence: not written to {0} — it holds {1} "
                                 "data and this process is {2}.\n".format(
                                     path, store_origin(), origin()))
            except Exception:
                pass
        return False
    line = json.dumps(clean(record), ensure_ascii=False, sort_keys=True)
    try:
        with _lock(path):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return True
    except Exception as error:
        if name not in _noted:
            _noted.add(name)
            try:
                import sys
                sys.stderr.write("ATLAS intelligence: could not write {0}: {1}\n"
                                 .format(path, error))
            except Exception:
                pass
        return False


def read(name, limit_bytes=MAX_READ_BYTES):
    """Every record in <folder>/<name>, oldest first (the newest part if big)."""
    path = folder() / name
    try:
        size = path.stat().st_size
    except OSError:
        return []
    out = []
    try:
        with open(path, "rb") as handle:
            if size > limit_bytes:
                handle.seek(size - limit_bytes)
                handle.readline()
            for raw in handle:
                try:
                    out.append(json.loads(raw.decode("utf-8")))
                except Exception:
                    continue
    except OSError:
        return []
    return out


def signature(name):
    """(size, mtime) — changes whenever the file does. For caches."""
    try:
        st = (folder() / name).stat()
        return (st.st_size, st.st_mtime)
    except OSError:
        return (0, 0)


def load_json(name, default):
    try:
        return json.loads((folder() / name).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(name, data):
    path = folder() / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with _lock(path):
            tmp.write_text(json.dumps(clean(data), indent=2, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(str(tmp), str(path))
        return True
    except Exception:
        return False


def now():
    return time.time()


def stamp(epoch=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch or time.time()))


def month_of(epoch):
    return time.strftime("%Y-%m", time.localtime(epoch))
