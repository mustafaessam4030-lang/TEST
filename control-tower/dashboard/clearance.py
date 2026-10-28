"""
Clearance: the eHub "Shipment Update" export, read on the server and shown
on /clearance (static/clearance.html), with the same sign-in and access
levels as the rest of the dashboard.

How an export arrives (either way):
  * uploaded on the Clearance page by someone with clearance.upload, or
  * dropped as .xlsx into the inbox folder on the server
    (ATA_CLEARANCE_INBOX, default C:\\Automation\\clearance) - the newest file
    is read when the page asks and it is newer than the one shown.

Only the "Export" sheet is read. Values are taken as they are: nothing is
repaired. A value that cannot be used (no ETA, an ETA year like 2926, a step
due date of 1899/1900, a cycle time from a missing date) becomes null and is
counted under data checks, which the page lists. No business value is
invented and nothing is written back to eHub.
"""

import datetime as _dt
import json
import os
import re as _re
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_UPLOAD = 40 * 1024 * 1024
FIELDS = ["bu", "div", "mode", "ffw", "agent", "status", "step", "eta", "ata", "ontime",
          "overdue", "awb", "ach", "cycle", "target", "due", "next", "submitted", "release",
          "completed"]
NEEDED = ("BU", "Mode", "Current Step", "ETA", "On Time", "Performance",
          "Expected Completion Date", "Last Date", "AWB/BOL")

_lock = threading.Lock()
_status = {"state": "idle", "message": None, "file": None, "started": None}
_SEEN = "inbox_seen.json"         # the inbox file last taken, kept apart from what is shown


def runtime():
    d = Path(os.environ.get("ATA_RUNTIME_DIR") or HERE / ".runtime") / "clearance"
    d.mkdir(parents=True, exist_ok=True)
    return d


def inbox():
    if os.environ.get("ATA_CLEARANCE_INBOX"):
        return Path(os.environ["ATA_CLEARANCE_INBOX"])
    return HERE.parent.parent / "clearance"        # C:\Automation\clearance


# ── reading an export ─────────────────────────────────────────────────────

def _date(v):
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime("%Y-%m-%d")
    s = str(v or "")[:10]
    return s if len(s) == 10 and s[4] == "-" and s[:4].isdigit() else None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def prepare(rows):
    """
    rows: the Export sheet as lists, header first. -> the page's data
    {asof, rows, dq, fields, count}. Raises ValueError when a needed column
    is missing (nothing is loaded then).
    """
    rows = iter(rows)
    header = [str(h or "").strip() for h in next(rows, [])]
    ix = {}
    for i, h in enumerate(header):
        ix.setdefault(h, i)
    missing = [n for n in NEEDED if n not in ix]
    if missing:
        raise ValueError("the Export sheet has no column " + ", ".join(missing))

    def g(r, c):
        i = ix.get(c)
        return r[i] if i is not None and i < len(r) else None

    dq = {"eta_missing": 0, "eta_bad_year": 0, "cycle_bad": 0, "due_bad": 0,
          "mode_missing": 0, "bu_missing": 0, "ontime_unclear": 0,
          "agent_name_variants": 0, "correction": 0}
    # A plausible ETA is within two years of today; 1899/1900 is Excel's
    # empty date. Neither window is a business rule, only "readable".
    this_year = _dt.date.today().year
    eta_lo, eta_hi = str(this_year - 2), str(this_year + 2)
    recs, asof = [], ""
    for r in rows:
        if not r or not any(v not in (None, "") for v in r):
            continue
        eta = _date(g(r, "ETA"))
        if not eta:
            dq["eta_missing"] += 1
        elif not (eta_lo <= eta[:4] <= eta_hi):
            dq["eta_bad_year"] += 1
            eta = None
        actual = _num(g(r, "Actual Days"))
        if actual is not None and not (0 <= actual <= 365):
            dq["cycle_bad"] += 1
            actual = None
        step = str(g(r, "Current Step") or "")
        is_open = step != "Completed"
        perf = _num(g(r, "Performance")) if is_open else None
        due = _date(g(r, "Expected Completion Date"))
        if is_open and not (due and due[:4] >= "2000"):
            dq["due_bad"] += 1
            perf, due = None, None
        if not g(r, "Mode"):
            dq["mode_missing"] += 1
        if not g(r, "BU"):
            dq["bu_missing"] += 1
        flag = str(g(r, "On Time") or "").strip().lower()
        if flag not in ("yes", "no"):
            dq["ontime_unclear"] += 1          # counted as not on time, and said so
        last = _date(g(r, "Last Date"))
        if last and last > asof:
            asof = last
        ach = "".join({"Yes": "Y", "No": "N"}.get(g(r, "{0} - Achieved".format(k)), "-")
                      for k in range(1, 10))
        recs.append([
            str(g(r, "BU") or "?"), str(g(r, "Division") or "?"), str(g(r, "Mode") or "Unknown"),
            str(g(r, "Forwarder") or ""), str(g(r, "Clearing Agent") or "").strip(),
            str(g(r, "Status") or ""), step, eta, _date(g(r, "ATA")),
            1 if flag == "yes" else 0, perf,
            str(g(r, "AWB/BOL") if g(r, "AWB/BOL") is not None else "").strip(), ach, actual,
            _num(g(r, "Expected Cycle Days")), due, str(g(r, "Stage (Next Step)") or ""),
            _date(g(r, "Submitted To BU")), _date(g(r, "CustomsReleaseDate")),
            _date(g(r, "Completed")),
        ])
    agents = {}
    for x in recs:
        if x[4]:
            agents.setdefault(x[4].replace(" ", "").upper(), set()).add(x[4])
    dq["agent_name_variants"] = sum(1 for v in agents.values() if len(v) > 1)
    dq["correction"] = sum(1 for x in recs if x[6] == "Correction Required")
    if not recs:
        raise ValueError("the Export sheet has no shipments")
    return {"asof": asof or None, "rows": recs, "dq": dq,
            "fields": FIELDS, "count": len(recs)}


def read_workbook(path):
    """The Export sheet of an eHub Shipment Update workbook -> prepare()."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        if "Export" not in wb.sheetnames:
            raise ValueError('the workbook has no sheet named "Export"')
        return prepare(wb["Export"].iter_rows(values_only=True, max_col=80))
    finally:
        wb.close()


# ── storing, status, inbox ────────────────────────────────────────────────

def _write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(str(tmp), str(path))


def meta():
    p = runtime() / "meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def data_path():
    return runtime() / "data.json"


def status():
    with _lock:
        out = {k: _status[k] for k in ("state", "message", "file", "started")}
    current = meta()
    if current:
        current = {k: v for k, v in current.items() if not k.startswith("inbox_")}
    out["current"] = current
    return out


def _load(path, name, by, source, keep):
    try:
        data = read_workbook(path)
        _write_json(data_path(), data)
        info = {"file": name, "source": source, "by": by, "count": data["count"],
                "asof": data["asof"], "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "dq": data["dq"]}
        info.update(keep or {})
        _write_json(runtime() / "meta.json", info)
        with _lock:
            _status.update(state="ready", message="Loaded {0} shipments from {1}.".format(
                data["count"], name), file=name)
    except Exception as error:                       # shown on the page, nothing replaced
        text = str(error) if isinstance(error, ValueError) else \
            "the file could not be read as an Excel workbook ({0})".format(type(error).__name__)
        with _lock:
            _status.update(state="error", message="{0}: {1}. The data shown was not changed."
                           .format(name, text), file=name)
    finally:
        if source == "upload":
            try:
                os.remove(str(path))
            except OSError:
                pass


def _reserve(name):
    """True when no export is being read; marks this one as being read."""
    with _lock:
        if _status["state"] == "reading":
            return False
        _status.update(state="reading", message="Reading {0}…".format(name), file=name,
                       started=time.strftime("%Y-%m-%dT%H:%M:%S"))
        return True


def _start(path, name, by, source, keep=None):
    threading.Thread(target=_load, args=(path, name, by, source, keep),
                     name="clearance-read", daemon=True).start()
    return True, "Reading {0}. This takes about a minute for a large export.".format(name)


def upload(data, filename, by):
    """An uploaded .xlsx: checked, saved, then read in the background."""
    name = Path(str(filename or "export.xlsx")).name[:120]
    if not data or len(data) > MAX_UPLOAD:
        return False, "Send one Excel file up to 40 MB."
    if not name.lower().endswith((".xlsx", ".xlsm")) or data[:2] != b"PK":
        return False, "That is not an Excel workbook (.xlsx)."
    if not _reserve(name):
        return False, "An export is already being read. Wait for it to finish."
    path = runtime() / "upload-{0}.xlsx".format(uuid.uuid4().hex)      # one file per upload
    try:
        path.write_bytes(data)
    except OSError as error:
        with _lock:
            _status.update(state="error", message="{0} could not be saved on the server ({1})."
                           .format(name, type(error).__name__))
        return False, "The file could not be saved on the server."
    return _start(path, name, by, "upload")


def check_inbox():
    """Read the newest .xlsx in the inbox when it is newer than what is shown."""
    folder = inbox()
    try:
        files = [(p.stat().st_mtime, p) for p in folder.glob("*.xls[xm]")
                 if not p.name.startswith("~$")]
    except OSError:                    # a file being saved or moved right now: next time
        return None
    if not files:
        return None
    mtime, newest = max(files, key=lambda x: x[0])
    stamp = {"inbox_file": newest.name, "inbox_mtime": mtime}
    seen_path = runtime() / _SEEN
    try:
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    # Only a file newer than the last one taken from the inbox: an upload in
    # between is never replaced by an older inbox file.
    if seen and (seen == stamp or mtime <= seen.get("inbox_mtime", 0)):
        return None
    if not _reserve(newest.name):
        return None
    _write_json(seen_path, stamp)      # taken once, even if it cannot be read
    return _start(newest, newest.name, "inbox folder", "inbox", keep=stamp)


# ── contacts, escalations and the contact log ─────────────────────────────
#
# Contacts are typed in by an Admin on the Clearance page; nothing is looked
# up or guessed. Call and Mail happen on the person's own phone / Outlook
# (tel: and mailto:): the platform sends nothing. What was done is recorded.

_store_lock = threading.Lock()
KINDS = ("agents", "bus", "forwarders", "coe")
EMAIL = _re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")
PHONE = _re.compile(r"^\+?[0-9 ()\-./]{6,24}$")


def _read_json(name, default):
    try:
        return json.loads((runtime() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def contacts():
    data = _read_json("contacts.json", {})
    return {k: dict(data.get(k) or {}) for k in KINDS}


def save_contacts(payload, by):
    """Replace the contact list. -> (ok, message). Every value is checked."""
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k) or {}, dict) for k in KINDS):
        return False, "Bad contact list."
    out = {}
    for kind in KINDS:
        out[kind] = {}
        for name, c in (payload.get(kind) or {}).items():
            if not isinstance(c, dict):
                return False, "Bad contact for {0}.".format(str(name)[:60])
            name = str(name or "").strip()[:120]
            c = c or {}
            person = str(c.get("person") or "").strip()[:80]
            emails = [e.strip() for e in _re.split(r"[;,]", str(c.get("email") or "")) if e.strip()]
            phone = str(c.get("phone") or "").strip()[:30]
            if not name or not (person or emails or phone):
                continue
            bad = [e for e in emails if not EMAIL.match(e)]
            if bad:
                return False, "Not an email address: {0} ({1})".format(bad[0][:60], name)
            if phone and not PHONE.match(phone):
                return False, "Not a phone number: {0} ({1})".format(phone, name)
            out[kind][name] = {"person": person, "email": "; ".join(emails[:5]), "phone": phone}
    out["updated_by"], out["updated_at"] = by, time.strftime("%Y-%m-%dT%H:%M:%S")
    with _store_lock:
        _write_json(runtime() / "contacts.json", out)
    return True, "Contacts saved."


def escalations():
    return _read_json("escalations.json", [])


def escalate(row, reason, by):
    """A new open escalation for one shipment (its facts copied from the export)."""
    reason = str(reason or "").strip()
    if not reason:
        return False, "Write why it is escalated.", None
    if not row or not row.get("awb"):
        return False, "That shipment is not in the loaded export.", None
    entry = {"id": uuid.uuid4().hex[:12], "awb": row["awb"], "bu": row["bu"], "mode": row["mode"],
             "step": row["step"], "overdue": row["overdue"], "due": row["due"],
             "agent": row["agent"], "reason": reason[:600], "by": by,
             "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "open"}
    with _store_lock:
        items = escalations()
        if any(e["awb"] == entry["awb"] and e["bu"] == entry["bu"] and e["status"] == "open" for e in items):
            return False, "{0} already has an open escalation.".format(entry["awb"]), None
        items.append(entry)
        _write_json(runtime() / "escalations.json", items)
    return True, "Escalated {0}.".format(entry["awb"]), entry


def resolve(esc_id, note, by):
    with _store_lock:
        items = escalations()
        for e in items:
            if e["id"] == esc_id and e["status"] == "open":
                e.update(status="resolved", resolved_by=by, resolved_note=str(note or "")[:600],
                         resolved_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                _write_json(runtime() / "escalations.json", items)
                return True, "Resolved {0}.".format(e["awb"]), e
    return False, "No open escalation with that id.", None


# ── ATLAS's email for a shipment's situation ──────────────────────────────
#
# Written from the loaded export and the contacts on file only: who owns the
# step decides who it goes to, the days past due decide how firm it is. It is
# a draft for the person's own Outlook; nothing here sends anything.

STEP_OWNER = {"Submit to BU": "coe", "Assign Clearing Agent": "bu", "Correction Required": "bu",
              "Customs Pre Entry": "agent", "Customs Declaration": "agent", "Duty Assessment": "agent",
              "Duty Payment": "bu", "Customs Release": "agent", "Awaiting Reciept": "bu"}
STEP_ASK = {
    "Submit to BU": "submit this shipment's documents to the BU in eHub",
    "Assign Clearing Agent": "assign a clearing agent to this shipment in eHub",
    "Correction Required": "correct this shipment's record in eHub so it can move on",
    "Customs Pre Entry": "complete the customs pre-entry",
    "Customs Declaration": "lodge the customs declaration",
    "Duty Assessment": "share the duty assessment",
    "Duty Payment": "arrange the duty payment (the clearing agent is in copy for the receipt)",
    "Customs Release": "obtain the customs release",
    "Awaiting Reciept": "confirm the goods are received in the warehouse and close the receipt in eHub",
}


def _step_name(step):
    return "Awaiting Receipt" if step == "Awaiting Reciept" else step


def _d(iso):
    if not iso:
        return "not in the export"
    try:
        return _dt.datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return iso


def draft_mail(row, sender=""):
    """
    -> {to, cc, subject, body, situation, missing} for one shipment row, or
    None when there is no row. Recipients come from contacts() only; a missing
    one is named in 'missing', never made up.
    """
    if not row:
        return None
    book = contacts()

    def emails(kind, name):
        c = (book.get(kind) or {}).get(name or "") or {}
        return [e.strip() for e in (c.get("email") or "").split(";") if e.strip()], c.get("person") or ""
    agent, agent_p = emails("agents", row["agent"])
    bu, bu_p = emails("bus", row["bu"])
    coe, coe_p = emails("coe", "COE")
    step, overdue = row["step"], row["overdue"]
    esc = next((e for e in escalations() if e["awb"] == row["awb"] and e["bu"] == row["bu"]
                and e["status"] == "open"), None)
    owner = STEP_OWNER.get(step)
    missing = []
    if step == "Completed":
        to, cc, greet = agent or bu, bu if agent else [], agent_p or bu_p
        situation = "completed - a thank-you / confirmation, nothing to chase"
        ask = ("Thank you - eHub shows this shipment as completed{0}. Please let us know if anything is "
               "still open on your side.".format(" on " + _d(row["completed"]) if row["completed"] else ""))
        tone = ""
    else:
        if owner == "agent":
            to, cc, greet = agent, bu, agent_p
            if not agent:
                missing.append("the clearing agent's email ({0})".format(row["agent"] or "no agent in eHub"))
        elif owner == "coe":
            to, cc, greet = coe, bu, coe_p
            if not coe:
                missing.append("the COE email (Contacts > COE)")
        else:
            to, cc, greet = bu, agent if step == "Duty Payment" else [], bu_p
            if not bu:
                missing.append("BU {0}'s email".format(row["bu"]))
        what = STEP_ASK.get(step, "move this shipment to its next step")
        if overdue is None:
            situation = "{0}; eHub has no usable due date for this step".format(_step_name(step))
            tone = ("eHub has no due date for this step, so we cannot tell whether it is late. "
                    "Please also check the dates on the eHub record.")
            ask = "Could you please {0}, and share the expected date?".format(what)
        elif overdue <= 0:
            situation = "{0}; due {1} ({2} day{3} left) - a reminder".format(
                _step_name(step), _d(row["due"]), -overdue, "" if overdue == -1 else "s")
            tone = "A friendly reminder: this step is due on {0}.".format(_d(row["due"]))
            ask = "Could you please {0} by then?".format(what)
        elif overdue <= 7:
            situation = "{0}; {1} day{2} past due - a follow-up".format(_step_name(step), overdue, "" if overdue == 1 else "s")
            tone = "This step was due on {0} and is now {1} day{2} late.".format(_d(row["due"]), overdue, "" if overdue == 1 else "s")
            ask = "Could you please {0} and share the expected completion date?".format(what)
        else:
            situation = "{0}; {1} days past due - urgent".format(_step_name(step), overdue)
            tone = ("URGENT: this step was due on {0} and is now {1} days late. It is holding the "
                    "shipment's clearance.".format(_d(row["due"]), overdue))
            ask = "Please {0} today, or tell us what is blocking it and by when it will be done.".format(what)
        if esc:
            situation += "; escalated"
            tone += " It has been escalated: {0}".format(esc["reason"].rstrip(".") + ".")
            if bu and not set(bu) <= set(to) | set(cc):
                cc = cc + bu
    subject = "{0}{1} - {2} ({3}){4}".format(
        "URGENT: " if overdue and overdue > 7 and step != "Completed" else "",
        row["awb"] or "Shipment", _step_name(step), row["bu"],
        ", {0} days past due".format(overdue) if overdue and overdue > 0 and step != "Completed" else "")
    facts = ["AWB/BOL: {0}".format(row["awb"] or "-"),
             "BU: {0} - {1} - {2}".format(row["bu"], row["mode"], row["div"]),
             "Current step: {0}".format(_step_name(step))]
    if step != "Completed":
        facts.append("Step due: {0}{1}".format(_d(row["due"]), " ({0} days past due)".format(overdue)
                                               if overdue and overdue > 0 else ""))
    facts += ["ETA: {0}{1}".format(_d(row["eta"]), " - ATA: {0}".format(_d(row["ata"])) if row["ata"] else ""),
              "Forwarder: {0}".format(row["ffw"] or "-"), "Clearing agent: {0}".format(row["agent"] or "-"),
              "eHub status: {0} (export data as of {1})".format(row["status"], _d((load() or {}).get("asof")))]
    body = "\n".join(["Dear {0},".format(greet) if greet else "Hello,", ""] +
                     ([tone, ""] if tone else []) + [ask, "", "Shipment details (from eHub):"] +
                     ["- " + f for f in facts] + ["", "Thank you,", sender or ""]).rstrip() + "\n"
    return {"to": "; ".join(to), "cc": "; ".join(e for e in cc if e not in to), "subject": subject,
            "body": body, "situation": situation, "missing": missing, "awb": row["awb"], "bu": row["bu"]}


def log_contact(row, kind, to, by):
    """Someone started a call or drafted a mail for a shipment (the act itself is on their PC)."""
    if kind not in ("call", "mail") or not row:
        return False
    # Only recipients that are on file are recorded (what someone types in the
    # mail's To box is theirs to send; the record names who was contacted).
    known = set()
    for group in contacts().values():
        for c in group.values():
            known |= {e.strip().lower() for e in c.get("email", "").split(";") if e.strip()}
            if c.get("phone"):
                known.add(_re.sub(r"[^\d+]", "", c["phone"]))
    said = [t.strip() for t in _re.split(r"[;,]", str(to or "")) if t.strip()]
    on_file = [t for t in said if t.lower() in known or _re.sub(r"[^\d+]", "", t.split()[-1]) in known]
    entry = {"awb": row["awb"], "bu": row["bu"], "kind": kind, "to": "; ".join(on_file)[:200],
             "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = runtime() / "contact_log.jsonl"
    with _store_lock:
        with open(str(path), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        if path.stat().st_size > 4 * 1024 * 1024:          # keep the newest ~2 MB
            lines = path.read_text(encoding="utf-8").splitlines()
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(path))
    return True


def contact_log(awb=None, limit=200, bu=None):
    try:
        lines = (runtime() / "contact_log.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if (awb is None or e.get("awb") == awb) and (bu is None or e.get("bu") in (None, bu)):
            out.append(e)
    return out[-limit:]


# ── reading the loaded export (ATLAS, the actions) ────────────────────────

_cache = {"mtime": None, "data": None}


def load():
    """The loaded export, or None. Read again only when it changed."""
    try:
        path = data_path()
        mtime = path.stat().st_mtime
        if _cache["mtime"] != mtime:
            _cache.update(mtime=mtime, data=json.loads(path.read_text(encoding="utf-8")))
        return _cache["data"]
    except (OSError, ValueError):
        return None


def records(data=None):
    data = data or load()
    if not data:
        return []
    f = data["fields"]
    return [dict(zip(f, r)) for r in data["rows"]]


def norm_awb(text):
    return _re.sub(r"[\s\-./]", "", str(text or "")).upper()


def find(awb, data=None):
    """Every shipment row whose AWB/BOL matches (spaces and dashes ignored)."""
    key = norm_awb(awb)
    return [r for r in records(data) if key and norm_awb(r["awb"]) == key] if key else []


def awbs_in(text, data=None):
    """AWB/BOL numbers of the loaded export named in a question."""
    words = set(norm_awb(w) for w in _re.findall(r"[A-Za-z0-9][A-Za-z0-9\- ]{4,24}[A-Za-z0-9]", text or ""))
    words |= set(norm_awb(w) for w in _re.findall(r"[A-Za-z0-9\-]{5,}", text or ""))
    found = []
    for r in records(data):
        k = norm_awb(r["awb"])
        if len(k) >= 5 and any(ch.isdigit() for ch in k) and k in words and r["awb"] not in found:
            found.append(r["awb"])
    return found


def is_open(r):
    return r["step"] != "Completed"


def past_due(r):
    return is_open(r) and r["overdue"] is not None and r["overdue"] > 0


# Where a BU clears its shipments, for the map: only where the BU code IS
# that country's ISO 3166 alpha-2 code (EG is Egypt). A code that is not
# (JR, LB…) is not guessed: the map lists it as "not on the map".
BU_COUNTRY = {"EG": ("818", "Egypt"), "GH": ("288", "Ghana"), "TZ": ("834", "Tanzania"),
              "NG": ("566", "Nigeria"), "KE": ("404", "Kenya"), "IQ": ("368", "Iraq"),
              "UG": ("800", "Uganda"), "DJ": ("262", "Djibouti"), "SL": ("694", "Sierra Leone"),
              "LR": ("430", "Liberia"), "ET": ("231", "Ethiopia")}


def places():
    """{BU: {"iso": ISO 3166 numeric, "country": name}} for the map."""
    return {bu: {"iso": iso, "country": name} for bu, (iso, name) in BU_COUNTRY.items()}


def map_summary(data=None):
    """Per BU, for the Overview's map: shipments, open, past due and where most
    of the past-due ones wait; plus the totals for its headline. Counted from
    the loaded export's rows; None when nothing is loaded."""
    data = data or load()
    if not data:
        return None
    rows = records(data)
    per, steps = {}, {}
    for r in rows:
        if not r["bu"]:
            continue
        x = per.setdefault(r["bu"], {"bu": r["bu"], "n": 0, "open": 0, "late": 0, "steps": {}})
        x["n"] += 1
        if is_open(r):
            x["open"] += 1
            if past_due(r):
                x["late"] += 1
                x["steps"][r["step"]] = x["steps"].get(r["step"], 0) + 1
                steps[r["step"]] = steps.get(r["step"], 0) + 1
    bus = []
    for x in per.values():
        top = max(x["steps"].items(), key=lambda kv: kv[1]) if x["steps"] else (None, 0)
        bus.append({"bu": x["bu"], "n": x["n"], "open": x["open"], "late": x["late"],
                    "top_step": top[0], "top_n": top[1]})
    bus.sort(key=lambda b: (-b["late"], -b["open"], b["bu"]))
    top = max(steps.items(), key=lambda kv: kv[1]) if steps else (None, 0)
    return {"asof": data.get("asof"), "total": len(rows), "open": sum(b["open"] for b in bus),
            "late": sum(b["late"] for b in bus), "top_step": top[0], "top_n": top[1],
            "bus": bus, "places": places()}


GROUPS = {"With BU": ("Submit to BU", "Assign Clearing Agent", "Correction Required"),
          "In customs": ("Customs Pre Entry", "Customs Declaration", "Duty Assessment",
                         "Duty Payment", "Customs Release"),
          "Receipt": ("Awaiting Reciept",)}


def query(filters=None, top=5, data=None):
    """
    Counts for any combination of bu / mode / div / step / agent / ffw / month /
    status / flag (open, pastdue, done, late) from the loaded export. None when
    nothing is loaded. Every number is counted from the rows; nothing estimated.
    """
    data = data or load()
    if not data:
        return None
    f = dict(filters or {})
    rows = records(data)
    def keep(r):
        for k in ("bu", "mode", "div", "step", "agent", "ffw", "status"):
            if f.get(k) and str(r[k]).lower() != str(f[k]).lower():
                return False
        if f.get("month") and not (r["eta"] or "").startswith(f["month"]):
            return False
        if f.get("group") and r["step"] not in GROUPS.get(f["group"], ()):
            return False
        flag = f.get("flag")
        if flag == "open" and not is_open(r) or flag == "pastdue" and not past_due(r) or \
                flag == "done" and is_open(r) or flag == "late" and r["ontime"]:
            return False
        return True
    sel = [r for r in rows if keep(r)]
    open_ = [r for r in sel if is_open(r)]
    late = sorted([r for r in open_ if past_due(r)], key=lambda r: -r["overdue"])

    def group(key, rs):
        out = {}
        for r in rs:
            out[r[key]] = out.get(r[key], 0) + 1
        return sorted(out.items(), key=lambda x: -x[1])
    open_esc = [e for e in escalations() if e["status"] == "open"]
    awbs = {r["awb"] for r in sel}
    return {"asof": data["asof"], "filters": f, "total": len(sel),
            "on_time": sum(r["ontime"] for r in sel), "open": len(open_), "past_due": len(late),
            "done": len(sel) - len(open_),
            "past_due_by_step": group("step", late), "past_due_by_bu": group("bu", late),
            "past_due_by_agent": group("agent", [r for r in late if r["agent"]]),
            "past_due_by_ffw": group("ffw", [r for r in late if r["ffw"]]),
            "by_mode": group("mode", sel),
            "longest": [{k: r[k] for k in ("awb", "bu", "mode", "step", "overdue", "due", "agent", "ffw")}
                        for r in late[:top]],
            "escalated_open": [e for e in open_esc if e["awb"] in awbs],
            "values": {k: sorted({r[k] for r in rows if r[k]}) for k in
                       ("bu", "mode", "div", "step", "agent", "ffw", "status")},
            "dq": data["dq"]}


def summary(bu=None, top=5):
    """Kept for callers of the first version: query() for one BU or all."""
    q = query({"bu": bu} if bu else None, top=top)
    if q is None:
        return None
    q["bu"] = bu
    q["bus"] = q["values"]["bu"]
    q["longest"] = [dict(x, days=x["overdue"]) for x in q["longest"]]
    return q
