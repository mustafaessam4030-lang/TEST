"""
Knowledge governance: how anything becomes — or stops being — what ATLAS
knows.

    propose     anyone with knowledge.propose: a new item, a correction to an
                item, or an observation ("on lane X, carrier Y does Z"). A
                proposal is NOT knowledge: it is not searched or answered.
    ingest      a reviewer (knowledge.review) loads an approved document (an
                SOP, training material, a regulator's text). Its sections are
                kept with their location (file, heading, page or lines); every
                definition found becomes a proposal, checked for duplicates,
                conflicts with what is known, dates and country rules, and
                instruction-like text. Nothing from it is answered until a
                reviewer approves it.
    review      a reviewer approves or rejects a proposal. One may not approve
                one's own proposal. An approved item is answered as approved;
                a correction becomes a new version and the old one is kept.
    status      a reviewer marks a repository item reviewed or approved, or
                supersedes or retires any item. Superseded and retired items
                are kept for history and never answered as current.

Everything is one append-only log, kb_changes.jsonl, in the ATLAS store
(intelligence/store.py: its folder, its production/test separation, its
secret redaction). The knowledge base is the repository's items with this log
replayed over them (apply()), so the log is also the audit history.

What ATLAS is asked in chat is never written here: a conversation does not
become knowledge.
"""
import contextlib
import hashlib
import json
import os
import re
import threading
import time
from datetime import date
from pathlib import Path

from . import store

LOG = "kb_changes.jsonl"
MAX_TEXT = 6000
_lock = threading.RLock()
INGEST_MAX_DEFINITIONS = 300

# Text that tries to instruct a model rather than state knowledge. Flagged for
# the reviewer; the knowledge engine never executes or obeys item text anyway.
INSTRUCTION = re.compile(
    r"ignore (all |the )?(previous|prior|above) (instructions|rules)|disregard (the |all )?(previous|above|rules)|"
    r"system prompt|you are (now )?(chatgpt|an ai|atlas)|act as|jailbreak|developer mode|"
    r"<\s*script|javascript:|override (the )?(security|policy|rules)|reveal (the )?(password|secret|token|key)|"
    r"send (it|this|the data) to|exfiltrat", re.I)
COUNTRY = re.compile(
    r"\b(ghana|egypt|nigeria|kenya|tanzania|uganda|ethiopia|iraq|jordan|lebanon|sudan|djibouti|liberia|"
    r"sierra leone|gra|nafeza|icums|kra|tra|ura)\b", re.I)
DATED = re.compile(r"\b(19|20)\d{2}\b|effective (from|on)|with effect from|as of\b|budget|finance act|"
                   r"circular|notice no", re.I)


class GovernanceError(Exception):
    pass


# ── the log ─────────────────────────────────────────────────────────────────

def _path():
    return store.folder() / LOG


def _clean(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v, depth + 1) for k, v in value.items() if not store.FORBIDDEN_KEYS.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(value)[:200]]
    if isinstance(value, str):
        return store.redact(value, MAX_TEXT)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return store.redact(str(value), 400)


@contextlib.contextmanager
def _locked():
    """One writer at a time: threads in this process (the server) and other
    processes (the command line) alike, through a lock file beside the log."""
    with _lock:
        path = str(_path()) + ".lock"
        fh = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                for _ in range(200):
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise GovernanceError("the knowledge log is busy; try again")
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def _append(event, precheck=None):
    """Append one event. `precheck()` runs under the same lock first, so a
    check-then-write (is the proposal still pending?) cannot race."""
    if not store.admitted():
        raise GovernanceError("this process may not write to the ATLAS store ({0} data)".format(store.store_origin()))
    with _locked():
        if precheck is not None:
            precheck()
        event = dict(event, at=time.strftime("%Y-%m-%dT%H:%M:%S"), seq=_next_seq())
        line = json.dumps(_clean(event), ensure_ascii=False, sort_keys=True)
        with open(_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    try:
        from . import kb
        kb.reset()
    except Exception:
        pass
    return event


def events():
    try:
        text = _path().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _next_seq():
    """The last event's seq + 1, read from the end of the log (called under
    the lock, so the log is not read whole for every event)."""
    try:
        with open(_path(), "rb") as fh:
            fh.seek(0, 2)
            end = pos = fh.tell()
            chunk = b""
            while pos > 0:
                step = min(8192, pos)
                pos -= step
                fh.seek(pos)
                chunk = fh.read(step) + chunk
                lines = [x for x in chunk[:end].split(b"\n") if x.strip()]
                if len(lines) >= 2 or (pos == 0 and lines):
                    for raw in reversed(lines):
                        try:
                            return int(json.loads(raw.decode("utf-8")).get("seq") or 0) + 1
                        except ValueError:
                            continue
                    if pos == 0:
                        break
    except OSError:
        pass
    return 1


def signature():
    try:
        st = _path().stat()
        return (st.st_size, st.st_mtime)
    except OSError:
        return None


def _new_id(prefix, seed):
    return "{0}-{1}".format(prefix, hashlib.sha1((seed + str(time.time())).encode()).hexdigest()[:10])


# ── state from the log ──────────────────────────────────────────────────────

def proposals():
    """Every proposal with its current status: pending, approved, rejected."""
    out = {}
    for e in events():
        if e.get("action") == "propose":
            out[e["proposal_id"]] = dict(e, status="pending", decisions=[])
        elif e.get("action") == "review" and e.get("proposal_id") in out:
            p = out[e["proposal_id"]]
            p["decisions"].append({k: e.get(k) for k in ("decision", "by", "at", "note")})
            p["status"] = {"approve": "approved", "reject": "rejected"}.get(e.get("decision"), p["status"])
    return out


def documents():
    """Ingested documents: the runtime sources items from them can cite."""
    return {e["doc"]["id"]: e["doc"] for e in events() if e.get("action") == "ingest" and e.get("doc")}


def history(item_id):
    """The log entries that touched one item, oldest first."""
    out = []
    for e in events():
        target = e.get("item_id") or (e.get("item") or {}).get("id")
        if target == item_id or e.get("superseded_by") == item_id:
            out.append({k: e.get(k) for k in ("seq", "at", "action", "by", "decision", "reason", "note",
                                               "proposal_id", "version", "status", "superseded_by")})
    return [h for h in out if h.get("action")]


def apply(kb):
    """Replay the log over the repository's items (called by kb.KB)."""
    for doc in documents().values():
        kb.sources.setdefault(doc["id"], {
            "id": doc["id"], "title": doc["title"], "publisher": doc.get("publisher") or "Mantrac (internal)",
            "path": doc.get("name"), "authority": doc.get("authority") or "internal", "kind": "document",
            "jurisdiction": doc.get("jurisdiction"), "sha256": doc.get("sha256"),
            "checked": {"on": (doc.get("ingested_at") or "")[:10], "note": "ingested by " + str(doc.get("by"))}})
    props = proposals()
    for e in events():
        act = e.get("action")
        if act == "review" and e.get("decision") == "approve":
            p = props.get(e.get("proposal_id"))
            if not p or p.get("kind") == "observation":
                continue
            item = dict(p.get("item") or {})
            iid = item.get("id")
            if not iid:
                continue
            prev = kb.items.get(iid)
            if prev is not None and prev.get("review_status") in ("superseded", "retired"):
                continue                      # never brought back by approving a change to it
            if prev is not None:
                kb.history_versions = getattr(kb, "history_versions", {})
                kb.history_versions.setdefault(iid, []).append(dict(prev))
                merged = dict(prev)
                merged.update(item)
                item = merged
                item["version"] = int(prev.get("version", 1)) + 1
            else:
                item.setdefault("version", 1)
                item.setdefault("created", e["at"][:10])
            item["updated"] = e["at"][:10]
            item["review_status"] = "approved"
            item["reviewed_by"], item["reviewed_at"] = e.get("by"), e["at"]
            item["origin"] = "review:" + p["proposal_id"]
            kb.items[iid] = item
        elif act == "status":
            it = kb.items.get(e.get("item_id"))
            if it is None:
                continue
            it = dict(it)
            status = e.get("status")
            if status in ("reviewed", "approved"):
                for key in ("superseded_by", "retired_reason"):
                    it.pop(key, None)         # reinstated, with its reason in the log
            it["review_status"] = status
            if status in ("reviewed", "approved"):
                it["reviewed_by"], it["reviewed_at"] = e.get("by"), e["at"]
            if status == "superseded":
                it["superseded_by"] = e.get("superseded_by")
            if status in ("superseded", "retired"):
                it["retired_reason"] = e.get("reason")
            it["updated"] = e["at"][:10]
            kb.items[it["id"]] = it


# ── checks a reviewer sees ──────────────────────────────────────────────────

def _words(text):
    from .kb import tokens
    return set(tokens(text))


def checks(item, kb=None):
    """What a reviewer must look at before approving: duplicates, conflicts,
    dates, country rules, instruction-like text, schema problems."""
    from . import kb as KB
    k = kb or KB.kb()
    out = {"duplicates": [], "conflicts": [], "flags": [], "problems": []}
    text = " ".join(str(item.get(f) or "") for f in ("term", "definition", "explanation", "example", "scenario"))
    if INSTRUCTION.search(text):
        out["flags"].append("instruction-like text: treat as untrusted; it will be shown as text, never followed")
    if COUNTRY.search(text) and item.get("scope") != "jurisdiction":
        out["flags"].append("names a country or authority: if it is a country rule, give scope 'jurisdiction', the country, the authority as source and a review date")
    if DATED.search(text):
        out["flags"].append("mentions dates or editions: check it is current and set effective_from / review_by")
    if item.get("scope") == "jurisdiction" or item.get("kind") == "rule":
        out["flags"].append("consequential claim (a rule): needs a reviewer who owns this area")
    names = []
    for n in [item.get("term"), item.get("acronym")] + list(item.get("synonyms") or []):
        names += KB.name_keys(n)
    mine = _words(item.get("definition"))
    for key in set(names):
        for iid in sorted(k.names.get(key, ())):
            if iid == item.get("id"):
                continue
            other = k.items[iid]
            theirs = _words(other.get("definition"))
            overlap = len(mine & theirs) / float(len(mine | theirs) or 1)
            entry = {"id": iid, "term": other["term"], "similarity": round(overlap, 2),
                     "review_status": other.get("review_status")}
            if overlap >= 0.5:
                out["duplicates"].append(entry)
            else:
                out["conflicts"].append(dict(entry, note="same name, different definition: a correction, "
                                                          "a context-dependent meaning, or a conflict"))
    out["problems"] = KB.validate(dict(item), dict(k.sources), set(k.items) | {item.get("id")})
    return out


# ── actions ─────────────────────────────────────────────────────────────────

ITEM_FIELDS = ("id", "kind", "term", "synonyms", "acronym", "full_form", "terms_ar", "definition", "definition_ar",
               "explanation", "domain", "category", "difficulty", "example", "scenario", "misconceptions",
               "related", "preconditions", "exceptions", "limitations", "sources", "evidence_status", "scope",
               "jurisdiction", "edition", "effective_from", "review_by", "kpi", "definitions")


def propose(item=None, by=None, kind="new", note=None, observation=None, provenance=None, _kb=None):
    """
    Record a proposal. kind: 'new' (a new item), 'correction' (changes to an
    existing item — only the fields given), 'observation' (a note for a
    reviewer, never itself knowledge). Returns the proposal with its checks.
    """
    if not by:
        raise GovernanceError("a proposal needs the proposer's identity")
    from . import kb as KB
    k = _kb or KB.kb()
    if kind == "observation":
        text = " ".join(str(observation or "").split())
        if len(text) < 10:
            raise GovernanceError("an observation needs at least a sentence")
        ev = _append({"action": "propose", "proposal_id": _new_id("obs", text), "kind": "observation",
                      "by": by, "observation": text[:2000], "note": note,
                      "flags": ["instruction-like text"] if INSTRUCTION.search(text) else []})
        return dict(ev, status="pending")
    if not isinstance(item, dict):
        raise GovernanceError("a proposal needs an item")
    item = {f: item[f] for f in ITEM_FIELDS if f in item}
    iid = item.get("id")
    if kind == "correction":
        if iid not in k.items:
            raise GovernanceError("a correction names an existing item id")
        merged = dict(k.items[iid])
        merged.update(item)
        check_item = merged
    elif kind == "new":
        if iid in k.items:
            raise GovernanceError("{0} already exists: propose a correction instead".format(iid))
        check_item = item
    else:
        raise GovernanceError("kind must be new, correction or observation")
    found = checks(check_item, k)
    ev = _append({"action": "propose", "proposal_id": _new_id("prop", str(iid)), "kind": kind, "by": by,
                  "item": item, "note": note, "checks": found, "provenance": provenance})
    return dict(ev, status="pending")


def _merged(item, k):
    """The item as approving it would make it: a correction over the current item."""
    iid = (item or {}).get("id")
    if iid in k.items:
        full = dict(k.items[iid])
        full.update(item)
        return full
    return dict(item or {})


def review(proposal_id, decision, by, note=None, acknowledge_flags=False):
    """Approve or reject a proposal. The proposer may not approve their own;
    a proposal with schema problems cannot be approved; one with flags only
    with acknowledge_flags and a note. The checks are run again at approval,
    against the knowledge as it is now, and a retired or superseded item is
    never brought back by approving a change to it."""
    if decision not in ("approve", "reject"):
        raise GovernanceError("decision must be approve or reject")
    if not by:
        raise GovernanceError("a review needs the reviewer's identity")
    from . import kb as KB

    def still_valid():
        p = proposals().get(proposal_id)
        if p is None:
            raise GovernanceError("no such proposal")
        if p["status"] != "pending":
            raise GovernanceError("this proposal is already {0}".format(p["status"]))
        if decision != "approve":
            return
        if str(p.get("by")).lower() == str(by).lower():
            raise GovernanceError("you proposed this; another reviewer must approve it")
        if p.get("kind") == "observation":
            raise GovernanceError("an observation is not knowledge: propose an item from it, then approve that")
        k = KB.kb()
        iid = (p.get("item") or {}).get("id")
        current = k.items.get(iid) or k.invalid.get(iid)
        if current is not None and current.get("review_status") in ("superseded", "retired"):
            raise GovernanceError("{0} is {1}; approving a change does not bring it back — reinstate it "
                                  "first (status, with a reason) if it should be current again".format(
                                      iid, current["review_status"]))
        if p.get("kind") == "new" and iid in k.items and k.items[iid].get("origin") != "review:" + proposal_id:
            raise GovernanceError("{0} now exists; propose this as a correction instead".format(iid))
        found = checks(_merged(p.get("item"), k), k)
        if found.get("problems"):
            raise GovernanceError("it has schema problems: " + "; ".join(found["problems"]))
        if (found.get("flags") or found.get("conflicts")) and not (acknowledge_flags and note):
            raise GovernanceError("it has flags or conflicts: approve with acknowledge_flags and a note saying why")

    pid_item = (proposals().get(proposal_id) or {}).get("item") or {}
    return _append({"action": "review", "proposal_id": proposal_id, "decision": decision, "by": by, "note": note,
                    "item_id": pid_item.get("id")}, precheck=still_valid)


def set_status(item_id, status, by, reason=None, superseded_by=None):
    """Mark an item reviewed or approved, or supersede or retire it."""
    from . import kb as KB
    k = KB.kb()
    if status not in ("reviewed", "approved", "superseded", "retired"):
        raise GovernanceError("status must be reviewed, approved, superseded or retired")
    if item_id not in k.items:
        raise GovernanceError("no such item" if item_id not in k.invalid else
                              "{0} fails its schema and cannot be given a status until it is corrected".format(item_id))
    if status == "superseded":
        if not superseded_by or superseded_by not in k.items or superseded_by == item_id:
            raise GovernanceError("supersede needs the id of the item that replaces it")
    if status in ("superseded", "retired") and not reason:
        raise GovernanceError("say why")
    if k.items[item_id].get("review_status") in ("superseded", "retired") and status in ("reviewed", "approved") \
            and not reason:
        raise GovernanceError("{0} is {1}: say why it is being reinstated".format(item_id, k.items[item_id]["review_status"]))
    if not by:
        raise GovernanceError("a status change needs the reviewer's identity")
    return _append({"action": "status", "item_id": item_id, "status": status, "by": by, "reason": reason,
                    "superseded_by": superseded_by})


# ── ingestion ───────────────────────────────────────────────────────────────

# "Term (ABC) is/means ...", "Term: ...", "**Term** — ...": a definition line.
DEF_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:\*\*)?(?P<term>[A-Z][A-Za-z0-9 /&'().-]{1,60}?)(?:\*\*)?\s*"
    r"(?:\((?P<acr>[A-Z][A-Z0-9/&-]{1,10})\))?\s*(?::|—|–| - | is | are | means | refers to )\s*(?P<def>.{20,600})$")


# A line that starts like this is a note, a step or a heading, not a term.
NOT_A_TERM = re.compile(
    r"^(this|that|these|those|it|its|there|here|note|nb|ps|step(\s*\d+)?|date|please|warning|caution|important|"
    r"tip|example|e\.g|i\.e|see|also|for|if|when|then|the|a|an|we|you|i|our|your|q|answer|question|subject|"
    r"from|to|cc|re|page|figure|table|version|approved( by)?|prepared( by)?|owner|status|remember|always|never|"
    r"do|don't|make sure|ensure|check)\b", re.I)


def _sections_from_text(text, name):
    sections, title, start, buf = [], name, 0, []
    lines = text.splitlines()
    for i, line in enumerate(lines + ["# END"], 1):
        if re.match(r"^#{1,4} ", line) or i == len(lines) + 1:
            if "".join(buf).strip():
                sections.append({"title": title, "location": "{0} › {1}".format(name, title),
                                 "first_line": start + 1, "text": "\n".join(buf)})
            title, start, buf = line.lstrip("#").strip(), i, []
        else:
            buf.append(line)
    return sections


def _sections_from_pdf(data, name):
    try:
        import fitz
    except Exception:
        raise GovernanceError("PDF ingestion needs PyMuPDF (pymupdf), which is not installed")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        return [{"title": "page {0}".format(i + 1), "location": "{0}, page {1}".format(name, i + 1),
                 "page": i + 1, "text": page.get_text()} for i, page in enumerate(doc)]
    except Exception:
        raise GovernanceError("the PDF could not be read (damaged, encrypted or not a PDF)")


def ingest(name, by, text=None, data=None, title=None, authority="internal", jurisdiction=None,
           domain="P", publisher=None):
    """
    Load an approved document. Returns {"doc", "proposals": [...], "skipped"}.
    Ingesting the same content twice adds nothing.
    """
    if not by:
        raise GovernanceError("ingestion needs the reviewer's identity")
    if authority not in ("internal", "standard", "government", "industry-body", "international-organisation"):
        raise GovernanceError("unknown source authority")
    raw = data if data is not None else (text or "").encode("utf-8")
    if not raw:
        raise GovernanceError("the document is empty")
    sha = hashlib.sha256(raw).hexdigest()
    for doc in documents().values():
        if doc.get("sha256") == sha:
            return {"doc": doc, "proposals": [], "skipped": "already ingested"}
    if data is not None and (str(name).lower().endswith(".pdf") or raw[:4] == b"%PDF"):
        sections = _sections_from_pdf(raw, name)
    elif str(name).lower().endswith(".pdf"):
        raise GovernanceError("send a PDF as its file (base64 data), not as text")
    else:
        sections = _sections_from_text(raw.decode("utf-8", "replace"), name)
    doc = {"id": "doc-" + sha[:10], "name": str(name)[:200], "title": (title or name)[:200], "sha256": sha,
           "authority": authority, "jurisdiction": jurisdiction, "publisher": publisher, "by": by,
           "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sections": len(sections)}
    _append({"action": "ingest", "doc": doc, "by": by})
    from . import kb as KB
    k = KB.kb()                       # one load for the whole document, not one per definition
    made, seen, dropped = [], set(), {"not_a_term": 0, "repeated": 0, "over_limit": 0}
    for sec in sections:
        for n, line in enumerate(sec["text"].splitlines()):
            m = DEF_LINE.match(line.strip())
            if not m:
                continue
            term = m.group("term").strip(" *")
            if len(term.split()) > 6 or NOT_A_TERM.match(term) or len(m.group("def").split()) < 4:
                dropped["not_a_term"] += 1
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")[:40] or "term"
            if slug in seen:
                dropped["repeated"] += 1          # the first definition in the document is proposed
                continue
            if len(made) >= INGEST_MAX_DEFINITIONS:
                dropped["over_limit"] += 1
                continue
            seen.add(slug)
            # An internal document states Mantrac's own rules ("business");
            # an outside one general or country knowledge. Either way the
            # item cites the document and the place in it; none is code-verified.
            scope = "jurisdiction" if jurisdiction else ("business" if authority == "internal" else "general")
            item = {"id": "{0}.{1}".format(domain, slug), "kind": "term", "domain": domain, "category": "ingested",
                    "difficulty": 1, "term": term, "definition": m.group("def").strip(),
                    "evidence_status": "source_cited", "scope": scope, "jurisdiction": jurisdiction,
                    "sources": [{"source": doc["id"], "page": sec.get("page"),
                                 "section": sec["location"] + ("" if sec.get("page") else ", line {0}".format(sec["first_line"] + n))}]}
            if m.group("acr"):
                item["acronym"] = m.group("acr")
            if jurisdiction:
                item["review_by"] = date.fromordinal(date.today().toordinal() + 182).isoformat()
            kind = "correction" if item["id"] in k.items else "new"
            if kind == "correction":
                item = {f: v for f, v in item.items() if f in ("id", "definition", "sources")}
            made.append(propose(item, by=by, kind=kind, note="ingested from " + doc["title"],
                                provenance={"doc": doc["id"], "location": item["sources"][0]["section"],
                                            "quote": line.strip()[:500]}, _kb=k))
    return {"doc": doc, "proposals": made, "skipped": None, "dropped": dropped}


def stats():
    props = proposals()
    by = {}
    for p in props.values():
        by[p["status"]] = by.get(p["status"], 0) + 1
    return {"proposals": by, "documents": len(documents()), "events": len(events())}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m intelligence.kb_govern",
                                 description="Review ATLAS's logistics knowledge from the command line.")
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("ingest", help="load an approved document (Markdown, text or PDF)")
    i.add_argument("file")
    i.add_argument("--by", required=True)
    i.add_argument("--title")
    i.add_argument("--authority", default="internal")
    i.add_argument("--jurisdiction")
    i.add_argument("--domain", default="P")
    sub.add_parser("pending", help="list pending proposals")
    r = sub.add_parser("review", help="approve or reject a proposal")
    r.add_argument("proposal_id")
    r.add_argument("decision", choices=("approve", "reject"))
    r.add_argument("--by", required=True)
    r.add_argument("--note")
    r.add_argument("--acknowledge-flags", action="store_true")
    s = sub.add_parser("status", help="mark an item reviewed/approved, or supersede/retire it")
    s.add_argument("item_id")
    s.add_argument("status", choices=("reviewed", "approved", "superseded", "retired"))
    s.add_argument("--by", required=True)
    s.add_argument("--reason")
    s.add_argument("--superseded-by")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "ingest":
            p = Path(a.file)
            out = ingest(p.name, a.by, data=p.read_bytes(), title=a.title, authority=a.authority,
                         jurisdiction=a.jurisdiction, domain=a.domain)
            print("{0}: {1} proposal(s){2}".format(out["doc"]["id"], len(out["proposals"]),
                                                   " — " + out["skipped"] if out["skipped"] else ""))
        elif a.cmd == "pending":
            for p in proposals().values():
                if p["status"] == "pending":
                    print(p["proposal_id"], p.get("kind"), (p.get("item") or {}).get("id") or p.get("observation", "")[:60],
                          "by", p.get("by"), "flags:", len((p.get("checks") or {}).get("flags") or []))
        elif a.cmd == "review":
            review(a.proposal_id, a.decision, a.by, note=a.note, acknowledge_flags=a.acknowledge_flags)
            print("done")
        elif a.cmd == "status":
            set_status(a.item_id, a.status, a.by, reason=a.reason, superseded_by=a.superseded_by)
            print("done")
        else:
            ap.print_help()
    except GovernanceError as e:
        print("refused:", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
