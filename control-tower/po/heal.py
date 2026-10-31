"""
ATLAS self-healing for PO jobs — test a stopped job again, in isolation,
and trust nothing until it is verified.

When a job stops (EXTRACTION_FAILED, VALIDATION_FAILED, PDF_UNREADABLE),
heal() runs it again from its OWN kept PDF and its OWN recorded eHub trail,
through the real pipeline (read → extract → validate → template), but in a
throw-away sandbox store with email off. Nothing touches eHub, the mailbox,
the job's state or its output:

    detect     the job's recorded stop (state, failure code, blocking fields)
    diagnose   the recorded failure and its fields — facts from the record
    plan       the one strategy: the current reader + full validation
    risk       level 2 (isolated, reversible: a temporary folder, deleted)
    test       the real pipeline in the sandbox, from the kept PDF
    verify     VERIFIED only when the sandbox job passes validation AND gets
               its template; any other end is STILL_BLOCKED
    recover    never automatic: the job keeps its state. Applying the fix is
               a re-run by a person (the existing, authorised path), which
               validates everything again
    learn      a fix is "verified" in the catalog only after a VERIFIED test;
               an attempt alone is never learned
    escalate   STILL_BLOCKED (or no kept PDF) → the job stays for a person

Bounded: one test per job per reader version (the same strategy is never
repeated), at most MAX_TESTS per job. A category can be disabled.
"""

import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from . import pipeline as P, store as S

HEALABLE = (S.EXTRACTION_FAILED, S.VALIDATION_FAILED, S.PDF_UNREADABLE)
MAX_TESTS = 3
VERIFIED, STILL_BLOCKED, NOT_TESTED = "VERIFIED", "STILL_BLOCKED", "NOT_TESTED"
_HERE = Path(__file__).resolve().parent


def reader_version():
    """The version of the code a test ran: the reader, the rules, the pipeline."""
    h = hashlib.sha256()
    for name in ("extract.py", "validate.py", "pipeline.py", "template.py"):
        path = _HERE / name
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()[:12]


def category(record):
    """What stopped: the failure code and the fields that blocked."""
    failure = record.get("failure") or {}
    blocking = sorted(n for n, f in (record.get("fields") or {}).items()
                      if (f or {}).get("status") in ("MISSING", "AMBIGUOUS", "MALFORMED"))
    return "{0}:{1}".format(failure.get("code") or record.get("state"),
                            ",".join(blocking) or "-")


class _Replay(object):
    """The job's own kept PDF and recorded eHub trail — no website is opened."""

    def __init__(self, record, data):
        self.record, self.data = record, data

    def fetch(self, reference):
        r = self.record
        return {"data": self.data, "filename": (r.get("document") or {}).get("filename"),
                "identifier": r.get("identifier"), "origin": "kept evidence", "url": None,
                "hub": r.get("hub") or {}, "trail": r.get("discovery") or {}}


def _catalog_path(store):
    return Path(store.folder) / "healing" / "knowledge.json"


def catalog(store):
    try:
        return json.loads(_catalog_path(store).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_catalog(store, data):
    path = _catalog_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    S.write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False))


def disable(store, cat, by, reason):
    """Switch a strategy off for a category (a faulty fix is rolled back)."""
    data = catalog(store)
    entry = data.setdefault(cat, {"category": cat, "history": []})
    entry.update(disabled=True, disabled_by=by, disabled_reason=reason)
    entry["history"].append({"at": S.now_iso(), "event": "disabled", "by": by,
                             "reason": reason})
    _save_catalog(store, data)


def known_fix(store, record):
    """A verified fix for this job's category, with this reader — or None."""
    entry = catalog(store).get(category(record)) or {}
    if entry.get("disabled") or entry.get("status") != "verified" or \
            entry.get("reader") != reader_version():
        return None
    return entry


def heal(store, record, config=None):
    """Test the stopped job again in isolation. Returns the attempt (also kept on
    the job as record["healing"], with a HEAL_TESTED event). Never changes the
    job's state, output or email."""
    version = reader_version()
    cat = category(record)
    tests = list(record.get("healing") or [])
    attempt = {"at": S.now_iso(), "reader": version, "category": cat, "risk": 2,
               "strategy": "re-read the kept PDF with the current reader; full validation "
                           "and template in a sandbox; email off"}
    if record.get("state") not in HEALABLE:
        return dict(attempt, outcome=NOT_TESTED, why="the job did not stop at extraction or "
                                                     "validation")
    entry = catalog(store).get(cat) or {}
    if entry.get("disabled"):
        return dict(attempt, outcome=NOT_TESTED, why="this strategy is disabled for " + cat)
    if any(t.get("reader") == version for t in tests):
        return dict(attempt, outcome=NOT_TESTED, why="already tested with this reader version")
    if len(tests) >= MAX_TESTS:
        return dict(attempt, outcome=NOT_TESTED, why="the test limit for this job is reached")
    sha = str((record.get("document") or {}).get("sha256") or "")
    kept = Path(store.folder) / "documents" / (sha + ".pdf")
    if not sha or not kept.is_file():
        attempt.update(outcome=STILL_BLOCKED, why="no kept PDF to test against — a person "
                                                  "reviews the job")
        return _record(store, record, attempt, tests)

    sandbox = Path(tempfile.mkdtemp(prefix="atlas_heal_"))
    t0 = time.monotonic()
    try:
        box = S.Store(folder=sandbox / "po", output_dir=sandbox / "out")
        trial = box.create(record["doctype"], record.get("reference"),
                           dict(record.get("request") or {}))
        cfg = dict(config or P.config_from_env())
        cfg.update(email_disabled=True, auto_send=False, recipient=None)
        trial = P.process(box, trial, _Replay(record, kept.read_bytes()), cfg,
                          sleep=lambda s: None)
        validation = trial.get("validation") or {}
        passed = bool(validation.get("passed")) and bool(trial.get("output")) and \
            trial["state"] in (S.SAVED, S.EMAIL_PREPARED)
        blocking = sorted(n for n, f in (trial.get("fields") or {}).items()
                          if (f or {}).get("status") in ("MISSING", "AMBIGUOUS", "MALFORMED"))
        attempt.update(
            outcome=VERIFIED if passed else STILL_BLOCKED, seconds=round(
                time.monotonic() - t0, 2), sandbox_state=trial["state"],
            validation_passed=bool(validation.get("passed")),
            checks=len(validation.get("checks") or []), blocking=blocking,
            template=bool(trial.get("output")),
            why=("the kept PDF now reads and passes every validation check; re-run the job "
                 "to apply (it validates again)") if passed else
                ((trial.get("failure") or {}).get("detail") or trial.get("progress") or
                 trial["state"])[:300],
            evidence={"document_sha256": sha,
                      "set_aside": [w.get("text") for f in (trial.get("fields") or {}).values()
                                    for w in (f or {}).get("set_aside") or []][:20],
                      "settled": {n: (f.get("resolution") or {}).get("rule")
                                  for n, f in (trial.get("fields") or {}).items()
                                  if (f or {}).get("resolution")}})
    except Exception as error:                       # a failed test is escalated, not hidden
        attempt.update(outcome=STILL_BLOCKED, why="the test itself failed: {0}: {1}".format(
            type(error).__name__, str(error)[:200]))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    return _record(store, record, attempt, tests)


def _record(store, record, attempt, tests):
    fresh = store.get(record["po_id"]) or record
    fresh["healing"] = tests + [attempt]
    store.save(fresh)
    store.event(fresh, "HEAL_TESTED", "atlas_healing", attempt["outcome"],
                evidence=(fresh.get("document") or {}).get("sha256"),
                category=attempt["category"], reader=attempt["reader"],
                why=attempt.get("why"))
    _learn(store, fresh, attempt)
    record.clear()
    record.update(fresh)
    return attempt


def _learn(store, record, attempt):
    data = catalog(store)
    entry = data.setdefault(attempt["category"], {"category": attempt["category"],
                                                  "status": "unverified", "history": []})
    entry["history"] = (entry.get("history") or [])[-49:] + [{
        "at": attempt["at"], "po_id": record["po_id"], "reader": attempt["reader"],
        "outcome": attempt["outcome"], "why": attempt.get("why")}]
    if attempt["outcome"] == VERIFIED and not entry.get("disabled"):
        entry.update(
            status="verified", reader=attempt["reader"],
            cause=(record.get("failure") or {}).get("detail", "")[:300],
            strategy=attempt["strategy"], evidence=attempt.get("evidence"),
            verification={"sandbox_state": attempt.get("sandbox_state"),
                          "validation_passed": True, "checks": attempt.get("checks")},
            applies_when="the same failure, read by reader {0}".format(attempt["reader"]),
            limitations="Valid only for this reader version: any change to the reader, the "
                        "rules or the pipeline needs a new verified test. Never applied "
                        "without a person re-running the job, which validates again.")
    _save_catalog(store, data)


def status_line(record):
    """One plain line for the dashboard, from the recorded tests only."""
    tests = record.get("healing") or []
    if not tests:
        return None
    last = tests[-1]
    if last["outcome"] == VERIFIED:
        return "ATLAS tested a fix on the kept PDF: verified — re-run the job to apply it"
    return "ATLAS tested the kept PDF again: still blocked — needs a person ({0})".format(
        str(last.get("why") or "")[:120])


def after_stop(store, record, config=None):
    """Called when a job ends: test it if it stopped where a test can help.
    Never raises and never changes the job's state."""
    try:
        if record and record.get("state") in HEALABLE:
            return heal(store, record, config)
    except Exception:                                 # pragma: no cover
        return None
    return None
