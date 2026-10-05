"""
The evidence store — real images only.

Two sources, and nothing else:

    browser_capture   a screenshot the automation's own browser took, with the
                      page's text captured at the same moment
    user_upload       an image an operator gave ATLAS

Every entry records where the file came from, when, for which run, shipment,
carrier and event, and its SHA-256, so an image can be shown as evidence and
checked later. Nothing is ever generated: if no real image exists, ATLAS says
so.

Verification screens are never stored. The automation does not capture one
(update_eta.take_screenshot checks the page first), and an upload that reads
as one is refused and deleted.
"""

import hashlib
import os
import re
import struct
import threading
from pathlib import Path

from . import store

FILE = "evidence.jsonl"
MAX_UPLOAD = 6 * 1024 * 1024
MAGIC = ((b"\x89PNG\r\n\x1a\n", "png", "image/png"),
         (b"\xff\xd8\xff", "jpg", "image/jpeg"),
         (b"RIFF", "webp", "image/webp"),
         (b"GIF8", "gif", "image/gif"))
_cache = {"sig": None, "rows": []}
_lock = threading.Lock()


def uploads_dir():
    path = store.folder() / "uploads"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def image_kind(head):
    for magic, ext, mime in MAGIC:
        if head.startswith(magic):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext, mime
    return None, None


def dimensions(path):
    """(width, height) from the file header, or (None, None). PNG and JPEG."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(26)
            if head.startswith(b"\x89PNG"):
                return struct.unpack(">II", head[16:24])
            if head.startswith(b"\xff\xd8"):
                handle.seek(2)
                while True:
                    marker = handle.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        break
                    size = struct.unpack(">H", handle.read(2))[0]
                    if marker[1] in (0xC0, 0xC1, 0xC2):
                        handle.read(1)
                        h, w = struct.unpack(">HH", handle.read(4))
                        return w, h
                    handle.seek(size - 2, 1)
    except Exception:
        pass
    return None, None


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _id(seed):
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def entries():
    """Every evidence entry, newest first. Cached until the index changes."""
    sig = store.signature(FILE)
    with _lock:
        if _cache["sig"] != sig:
            rows = store.read(FILE)
            removed = {r.get("id") for r in rows if r.get("removed")}
            _cache["rows"] = [r for r in reversed(rows)
                              if r.get("id") and not r.get("removed")
                              and r.get("id") not in removed]
            _cache["sig"] = sig
        return list(_cache["rows"])


def register_capture(path, run_id=None, reference=None, carrier=None,
                     provider=None, event=None, step=None, text_path=None):
    """Index a screenshot the automation's browser just took. None if absent."""
    path = Path(path)
    try:
        if not path.is_file():
            return None
        with open(path, "rb") as handle:
            ext, mime = image_kind(handle.read(16))
        if ext is None:
            return None
        width, height = dimensions(path)
        entry = {
            "id": _id("{0}|{1}".format(path, store.now())),
            "source": "browser_capture", "path": str(path.resolve()),
            "text_path": str(Path(text_path).resolve()) if text_path and
            Path(text_path).is_file() else None,
            "mime": mime, "bytes": path.stat().st_size, "sha256": _sha(path),
            "width": width, "height": height, "run_id": run_id,
            "reference": reference, "carrier": carrier, "provider": provider,
            "event": event, "step": step, "at": store.stamp(),
            "epoch": round(store.now(), 1),
        }
    except Exception:
        return None
    store.append(FILE, entry)
    return entry


def register_upload(data, filename="upload", reference=None, read=None):
    """
    Store an operator's image. (ok, entry_or_message).

    `read(path)` — vision.read_image — runs before the file is kept; an image
    that shows a verification screen is deleted and refused.
    """
    if not data:
        return False, "No image came with that request."
    if len(data) > MAX_UPLOAD:
        return False, "That image is larger than 6 MB."
    ext, mime = image_kind(data[:16])
    if ext is None:
        return False, "That is not a PNG, JPEG, WebP or GIF image."
    digest = hashlib.sha256(data).hexdigest()
    target = uploads_dir() / "{0}.{1}".format(digest[:20], ext)
    try:
        target.write_bytes(data)
    except Exception as error:
        return False, "The image could not be saved: {0}".format(error)
    result = read(target) if read else None
    if result and result.get("verification_screen"):
        try:
            target.unlink()
        except Exception:
            pass
        return False, result.get("message")
    width, height = dimensions(target)
    entry = {
        "id": _id("{0}|{1}".format(digest, store.now())), "source": "user_upload",
        "path": str(target.resolve()), "text_path": None, "mime": mime,
        "bytes": len(data), "sha256": digest, "width": width, "height": height,
        "name": re.sub(r"[^A-Za-z0-9._ -]", "", str(filename))[:80] or "upload",
        "run_id": None, "reference": reference, "carrier": None, "provider": None,
        "event": "uploaded", "step": None, "at": store.stamp(),
        "epoch": round(store.now(), 1),
    }
    store.append(FILE, entry)
    return True, entry


SYNCED_FIELDS = ("id", "source", "mime", "bytes", "sha256", "width", "height", "run_id",
                 "reference", "carrier", "provider", "event", "step", "at", "epoch")


def register_synced(entry, data, via, text=None):
    """
    A capture the worker's automation took, forwarded to the control plane:
    kept only if it is an image whose SHA-256 is the one in its index line,
    and only as a browser capture (the worker never forwards uploads).
    Idempotent: the same id twice is stored once. (ok, message)
    """
    if not isinstance(entry, dict) or not data:
        return False, "No capture came with that request."
    if entry.get("source") != "browser_capture" or not re.match(
            r"^[0-9a-f]{12}$", str(entry.get("id") or "")):
        return False, "Only automation captures are synced."
    if len(data) > MAX_UPLOAD:
        return False, "That capture is larger than 6 MB."
    ext, mime = image_kind(data[:16])
    if ext is None:
        return False, "That is not an image."
    digest = hashlib.sha256(data).hexdigest()
    if entry.get("sha256") != digest:
        return False, "The capture does not match its index entry."
    if find(entry["id"]) is not None:
        return True, "Already stored."
    target_dir = store.folder() / "synced"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "{0}.{1}".format(digest[:20], ext)
        target.write_bytes(data)
        text_path = None
        if text:
            text_path = target.with_suffix(".txt")
            text_path.write_text(store.redact(text, 200000), encoding="utf-8")
    except Exception as error:
        return False, "The capture could not be saved: {0}".format(error)
    new = {k: entry.get(k) for k in SYNCED_FIELDS}
    new.update(path=str(target.resolve()), mime=mime,
               text_path=str(text_path.resolve()) if text_path else None,
               via=str(via)[:40])
    if not store.append(FILE, new):
        return False, "The evidence store refused it (data origin differs)."
    return True, "Stored."


def find(evidence_id):
    for entry in entries():
        if entry.get("id") == evidence_id:
            return entry
    return None


def search(reference=None, run_id=None, carrier=None, provider=None, event=None,
           source=None, failures_only=False, limit=20):
    out = []
    for e in entries():
        if reference and e.get("reference") != reference:
            continue
        if run_id and e.get("run_id") != run_id:
            continue
        if source and e.get("source") != source:
            continue
        if event and event not in str(e.get("event") or ""):
            continue
        hay = " ".join(str(x) for x in (e.get("carrier"), e.get("provider"))).casefold()
        if carrier and carrier.casefold() not in hay:
            continue
        if provider and provider.casefold() not in hay:
            continue
        if failures_only and not re.search(r"error|fail|exhausted|timeout",
                                           str(e.get("event") or ""), re.I):
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def allowed_roots():
    roots = [uploads_dir().resolve()]
    extra = os.environ.get("ATLAS_EVIDENCE_ROOTS")
    if extra:
        roots += [Path(p).resolve() for p in extra.split(os.pathsep) if p]
    return roots


def file_for(evidence_id):
    """
    The real file behind an entry — only if it is indexed, still exists,
    still has the hash it was indexed with, and is an image.
    """
    entry = find(evidence_id)
    if entry is None:
        return None, None
    path = Path(entry.get("path") or "")
    try:
        if not path.is_file():
            return entry, None
        if entry.get("source") == "user_upload":
            root = uploads_dir().resolve()
            if root not in path.resolve().parents:
                return entry, None
        with open(path, "rb") as handle:
            if image_kind(handle.read(16))[0] is None:
                return entry, None
        if entry.get("sha256") and _sha(path) != entry["sha256"]:
            return entry, None          # changed since capture: not evidence
    except Exception:
        return entry, None
    return entry, path


def page_text(entry, limit=20000):
    path = entry and entry.get("text_path")
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return None


def describe(entry):
    """Where an image came from, in one line."""
    if entry.get("source") == "user_upload":
        return "Uploaded to ATLAS at {0}{1}.".format(
            entry.get("at"), " for " + entry["reference"] if entry.get("reference") else "")
    bits = ["Captured by the automation's browser"]
    if entry.get("run_id"):
        bits.append("during run {0}".format(entry["run_id"]))
    if entry.get("at"):
        bits.append("at {0}".format(entry["at"]))
    head = " ".join(bits)
    what = []
    if entry.get("reference"):
        what.append(str(entry["reference"]))
    if entry.get("carrier") or entry.get("provider"):
        what.append(str(entry.get("carrier") or entry.get("provider")))
    if entry.get("event"):
        what.append("event: " + str(entry["event"]).replace("_", " "))
    return head + (" — " + ", ".join(what) if what else "") + "."


def public(entry):
    keep = ("id", "source", "mime", "bytes", "sha256", "width", "height", "run_id",
            "reference", "carrier", "provider", "event", "step", "at", "name")
    out = {k: entry.get(k) for k in keep}
    out["has_page_text"] = bool(entry.get("text_path"))
    out["url"] = "/api/evidence/file?id=" + str(entry.get("id"))
    out["provenance"] = describe(entry)
    return out
