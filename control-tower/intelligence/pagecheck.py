"""
"Has this carrier's page changed?" — answered from evidence, with a stated
confidence, and never from a single missing search box.

A tracking box that cannot be found has many ordinary causes before it means
the site was redesigned: the page had not finished loading, the network or the
carrier was down, a human check was showing, the carrier refused this machine,
the page had moved. Each observation is therefore CLASSIFIED first:

    NETWORK            navigation failed, the server answered 5xx, or the
                       carrier could not be reached
    CHALLENGE          a human-verification check was on the page
    RESTRICTED         the carrier's own "access restricted" page
    PAGE_MOVED         the address answers 404/410 or "page not found"
    LOADING            the page never finished loading, or rendered nearly
                       nothing
    LAYOUT_CANDIDATE   none of the above: the page loaded, was reachable, had
                       no challenge — and still had no tracking box

Only PAGE_MOVED and LAYOUT_CANDIDATE can become a site-change alert, and
how sure ATLAS is depends on repetition (assess()):

    CONFIRMED  seen in 2+ separate runs, the page looking the same each time
               (same set of input boxes), and no successful lookup since
    LIKELY     seen 2+ times in one run with the same page — or a 404, which
               is strong evidence on its own
    POSSIBLE   seen once

A later successful lookup on that carrier closes the alert (RESOLVED).
Observations are written by the automation (production runs only) to
carrier_pages.jsonl in the ATLAS store; this module reads them back.
"""

import hashlib
import re

from . import store

FILE = "carrier_pages.jsonl"
CAUSES = ("NETWORK", "CHALLENGE", "RESTRICTED", "PAGE_MOVED", "LOADING",
          "LAYOUT_CANDIDATE")
CHANGE_CAUSES = ("PAGE_MOVED", "LAYOUT_CANDIDATE")
MIN_TEXT = 200
NOT_FOUND = re.compile(r"\b(?:404|page\s+not\s+found|page\s+(?:can(?:no|')t|could\s+not)"
                       r"\s+be\s+found|we\s+can(?:no|')t\s+find\s+this\s+page)\b", re.I)
# update_eta.probe_host answers "NO REPLY after …" when the host cannot be
# reached, and "HTTP/1.1 200 OK | connected in …" when it can.
PROBE_BAD = re.compile(r"^\s*NO REPLY\b")


def signature(inputs):
    """A stable fingerprint of the page's visible input boxes (their
    placeholder/name/id/type), so 'the page looks the same' can be checked
    across runs without storing the page."""
    items = sorted("|".join(str(i.get(k) or "") for k in ("placeholder", "name", "id", "type"))
                   for i in (inputs or []))
    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()[:12]


def classify(evidence):
    """One observation -> {"cause", "reasons": [..]}. Ordered: the first
    cause the evidence supports wins, so a page that is both slow and behind a
    challenge is a CHALLENGE, not a redesign."""
    e = evidence or {}
    reasons = []
    status = e.get("http_status")
    if not e.get("navigated"):
        return {"cause": "NETWORK", "reasons": [
            "the page did not open: {0}".format(e.get("nav_error") or "navigation failed")]}
    if isinstance(status, int) and status >= 500:
        return {"cause": "NETWORK", "reasons": ["the server answered HTTP {0}".format(status)]}
    carrier_probe, control_probe = e.get("carrier_probe") or "", e.get("control_probe") or ""
    if carrier_probe and PROBE_BAD.search(carrier_probe):
        reasons.append("the carrier host did not answer a plain connection ({0})".format(
            carrier_probe[:80]))
        if control_probe and PROBE_BAD.search(control_probe):
            reasons.append("nor did an unrelated site: this machine's network")
        return {"cause": "NETWORK", "reasons": reasons}
    if e.get("captcha"):
        return {"cause": "CHALLENGE", "reasons": ["a human-verification check was showing"]}
    if e.get("restricted"):
        return {"cause": "RESTRICTED", "reasons": ["the carrier's access-restricted page"]}
    title, text_len = str(e.get("title") or ""), int(e.get("text_len") or 0)
    if status in (404, 410) or NOT_FOUND.search(title) or (
            text_len < 1500 and NOT_FOUND.search(str(e.get("excerpt") or ""))):
        return {"cause": "PAGE_MOVED", "reasons": [
            "the address answered {0}".format(
                "HTTP {0}".format(status) if status in (404, 410) else "'page not found'")]}
    if str(e.get("ready_state") or "") != "complete":
        return {"cause": "LOADING", "reasons": [
            "the page had not finished loading (state '{0}')".format(e.get("ready_state"))]}
    if text_len < MIN_TEXT:
        return {"cause": "LOADING", "reasons": [
            "the page rendered almost nothing ({0} characters)".format(text_len)]}
    return {"cause": "LAYOUT_CANDIDATE", "reasons": [
        "the page loaded ({0} characters, state complete), the carrier was reachable "
        "and no check was showing — but no tracking box matched".format(text_len)]}


def record(carrier, label, run_id, evidence, reference=None):
    """Append one observation (classified here). Never raises."""
    try:
        result = classify(evidence)
        compact = {k: evidence.get(k) for k in (
            "url", "final_url", "http_status", "ready_state", "text_len", "title",
            "input_count", "input_signature", "captcha", "restricted",
            "carrier_probe", "control_probe", "waited_ms", "page_text_file",
            "screenshot_file")}
        store.append(FILE, {"kind": "missing_box", "carrier": carrier, "label": label,
                            "run_id": run_id, "reference": reference,
                            "cause": result["cause"], "reasons": result["reasons"],
                            "evidence": compact, "ts": store.now()})
        return result
    except Exception:
        return classify(evidence)


def record_ok(carrier, label, run_id):
    """A lookup on this carrier found its box. Written once per carrier per
    run by the caller; closes any open alert."""
    try:
        store.append(FILE, {"kind": "box_found", "carrier": carrier, "label": label,
                            "run_id": run_id, "ts": store.now()})
    except Exception:
        pass


def assess(records=None):
    """-> list of per-carrier findings, newest evidence first. Pure over
    `records` (read from the store when None)."""
    records = store.read(FILE) if records is None else records
    by_carrier = {}
    for r in records:
        if r.get("carrier"):
            by_carrier.setdefault(r["carrier"], []).append(r)
    findings = []
    for carrier, rows in by_carrier.items():
        rows.sort(key=lambda r: str(r.get("ts") or ""))
        last_ok = max((i for i, r in enumerate(rows) if r.get("kind") == "box_found"),
                      default=-1)
        open_rows = [r for r in rows[last_ok + 1:] if r.get("kind") == "missing_box"]
        label = next((r.get("label") for r in reversed(rows) if r.get("label")), carrier)
        if not open_rows:
            if last_ok >= 0 and any(r.get("kind") == "missing_box" for r in rows[:last_ok]):
                findings.append({"carrier": carrier, "label": label, "status": "RESOLVED",
                                 "confidence": None, "cause": None,
                                 "why": "a later lookup found the tracking box again",
                                 "last_seen": rows[last_ok].get("ts")})
            continue
        change = [r for r in open_rows if r.get("cause") in CHANGE_CAUSES]
        other = [r for r in open_rows if r.get("cause") not in CHANGE_CAUSES]
        causes = {}
        for r in open_rows:
            causes[r.get("cause")] = causes.get(r.get("cause"), 0) + 1
        finding = {"carrier": carrier, "label": label, "observations": len(open_rows),
                   "causes": causes, "first_seen": open_rows[0].get("ts"),
                   "last_seen": open_rows[-1].get("ts"),
                   "evidence": [f for r in open_rows[-3:] for f in (
                       (r.get("evidence") or {}).get("page_text_file"),
                       (r.get("evidence") or {}).get("screenshot_file")) if f],
                   "reasons": open_rows[-1].get("reasons") or []}
        if not change:
            top = max(causes, key=causes.get)
            finding.update(status="NOT_A_SITE_CHANGE", confidence=None, cause=top,
                           why="every missing-box observation since the last good lookup "
                               "has an ordinary cause ({0})".format(
                                   ", ".join("{0} x{1}".format(k, v)
                                             for k, v in sorted(causes.items()))))
            findings.append(finding)
            continue
        runs = sorted({r.get("run_id") for r in change if r.get("run_id")})
        signatures = {(r.get("evidence") or {}).get("input_signature") for r in change
                      if r.get("cause") == "LAYOUT_CANDIDATE"}
        moved = [r for r in change if r.get("cause") == "PAGE_MOVED"]
        stable = len(signatures) <= 1
        if moved:
            confidence = "CONFIRMED" if len({r.get("run_id") for r in moved}) >= 2 else "LIKELY"
            cause = "PAGE_MOVED"
        elif len(runs) >= 2 and stable:
            confidence, cause = "CONFIRMED", "LAYOUT_CANDIDATE"
        elif len(change) >= 2 and stable:
            confidence, cause = "LIKELY", "LAYOUT_CANDIDATE"
        else:
            confidence, cause = "POSSIBLE", "LAYOUT_CANDIDATE"
        why = ["{0} observation(s) in {1} run(s) since the last successful lookup".format(
            len(change), len(runs) or 1)]
        if cause == "LAYOUT_CANDIDATE":
            why.append("the page looked the same each time" if stable
                       else "the page looked different between observations, "
                            "so this may be an unsettled or flaky page")
        if other:
            why.append("{0} other observation(s) had ordinary causes".format(len(other)))
        finding.update(status="SITE_CHANGE", confidence=confidence, cause=cause,
                       runs=len(runs), why="; ".join(why))
        findings.append(finding)
    order = {"SITE_CHANGE": 0, "NOT_A_SITE_CHANGE": 1, "RESOLVED": 2}
    rank = {"CONFIRMED": 0, "LIKELY": 1, "POSSIBLE": 2, None: 3}
    findings.sort(key=lambda f: (order[f["status"]], rank[f.get("confidence")],
                                 str(f.get("last_seen") or "")))
    return findings


def describe(finding):
    """One plain line for the briefing and ATLAS."""
    if finding["status"] == "SITE_CHANGE":
        what = ("its tracking address no longer opens (page not found)"
                if finding["cause"] == "PAGE_MOVED"
                else "its page loads but the tracking box is no longer where it was")
        return "{0}: site change {1} — {2}. {3}.".format(
            finding["label"], finding["confidence"], what, finding["why"])
    if finding["status"] == "NOT_A_SITE_CHANGE":
        return "{0}: tracking box not found, but not a site change — {1}.".format(
            finding["label"], finding["why"])
    return "{0}: resolved — {1}.".format(finding["label"], finding["why"])
