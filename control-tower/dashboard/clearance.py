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


# ── for ATLAS ─────────────────────────────────────────────────────────────

def summary(bu=None, top=5):
    """Counts from the loaded export for ATLAS, or None when nothing is loaded."""
    try:
        data = json.loads(data_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    F = {k: i for i, k in enumerate(data["fields"])}
    rows = [r for r in data["rows"] if not bu or r[F["bu"]] == bu]
    open_ = [r for r in rows if r[F["step"]] != "Completed"]
    late = sorted([r for r in open_ if r[F["overdue"]] is not None and r[F["overdue"]] > 0],
                  key=lambda r: -r[F["overdue"]])
    by_step, by_bu = {}, {}
    for r in late:
        by_step[r[F["step"]]] = by_step.get(r[F["step"]], 0) + 1
        by_bu[r[F["bu"]]] = by_bu.get(r[F["bu"]], 0) + 1
    on = sum(r[F["ontime"]] for r in rows)
    return {"asof": data["asof"], "bu": bu, "total": len(rows), "on_time": on,
            "open": len(open_), "past_due": len(late),
            "past_due_by_step": sorted(by_step.items(), key=lambda x: -x[1]),
            "past_due_by_bu": sorted(by_bu.items(), key=lambda x: -x[1]),
            "longest": [{"awb": r[F["awb"]], "bu": r[F["bu"]], "mode": r[F["mode"]],
                         "step": r[F["step"]], "days": r[F["overdue"]], "due": r[F["due"]]}
                        for r in late[:top]],
            "bus": sorted({r[F["bu"]] for r in data["rows"]}), "dq": data["dq"]}
