"""
The PO production-readiness gate — decided from evidence on record, never
from how the dashboard looks.

    NOT READY                    any mandatory pilot gate is not proven
    READY FOR CONTROLLED PILOT   automated tests green AND, on the real
                                 Windows worker against the real eHub: a real
                                 record discovered, Under Clearance, Manage,
                                 the real Bill Entry, a real PDF read, fields
                                 extracted, validated, template generated and
                                 saved; a controlled email accepted by Graph
                                 and found in Sent Items; the audit trail whole
    PRODUCTION READY             the pilot gates, plus: enough real jobs
                                 confirmed end to end, failure/recovery proven
                                 on real jobs, duplicate prevention observed,
                                 and a recorded security/business sign-off

Every gate is PASS, FAIL or UNVERIFIED, with the evidence that decided it.

READINESS LEVELS, kept apart so one green level never reads as another:

    A CODE_READY            the PO engine suites pass (test_po, test_po_hardening,
                            test_po_engine) in the last complete run
    B TEST_READY            the last complete run of every suite is green
    C SECURITY_READY        the automated security checks pass (release hygiene,
                            dashboard key) AND a human security review is recorded
    D ENVIRONMENT_READY     this machine is configured: Graph settings, the
                            recipient, the eHub credentials file, a writable
                            output folder (presence only — never the values)
    E REAL_WORLD_VERIFIED   the pilot gates on REAL / VERIFIED jobs
    F PRODUCTION_READY      E plus the production gates and the sign-off
A stand-in, test or simulated job never passes a REAL gate: only jobs whose
document provenance is REAL / VERIFIED (observed in the real eHub session by
the worker's own browser) count.
"""

import json
import os
from pathlib import Path

from . import quality, store as S

HERE = Path(__file__).resolve().parent.parent
PILOT_MIN_REAL_SAVED = 1
PRODUCTION_MIN_REAL_CONFIRMED = int(os.environ.get("PO_PRODUCTION_MIN_JOBS") or 20)
CHAIN = ("PO_DISCOVERED", "EHUB_RECORD_FOUND", "CLEARANCE_CHECKED", "MANAGE_OPENED",
         "BILL_ENTRY_FOUND", "PDF_FOUND", "PDF_READ", "FIELDS_EXTRACTED", "VALIDATION_PASSED",
         "TEMPLATE_GENERATED", "OUTPUT_SAVED")


def _real(record):
    p = record.get("provenance") or {}
    return p.get("source") == "REAL" and p.get("verification") == "VERIFIED"


def _gate(name, status, evidence):
    return {"gate": name, "status": status, "evidence": evidence}


def _tests():
    path = Path(os.environ.get("PO_TEST_RESULTS") or (HERE / "test_results.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _gate("Automated tests green", "UNVERIFIED",
                     "no test results file ({0}); run python run_tests.py".format(path.name))
    if not data.get("complete"):
        return _gate("Automated tests green", "UNVERIFIED",
                     "the results file is not from a complete run of python run_tests.py")
    ok = data.get("failed") == 0 and not data.get("broken")
    return _gate("Automated tests green", "PASS" if ok else "FAIL",
                 "{0} passed, {1} failed, {2} skipped{3} ({4})".format(
                     data.get("passed"), data.get("failed"), data.get("skipped", 0),
                     "; problems: " + ", ".join(data.get("broken")) if data.get("broken") else "",
                     data.get("at")))


def evaluate(store=None):
    store = store or S.Store()
    records = store.all(5000)
    real = [r for r in records if _real(r)]

    def reached(r, state):
        return any(h.get("state") == state for h in r.get("history") or []) or r["state"] == state

    saved = [r for r in real if reached(r, S.SAVED)]
    gates = [_tests()]
    gates.append(_gate("Real eHub reached from the worker; real record discovered",
                       "PASS" if real else "UNVERIFIED",
                       "{0} job(s) with a document observed in the real eHub session".format(
                           len(real)) if real else "no job with REAL / VERIFIED provenance yet "
                       "— run verify_po.bat on the Windows worker"))
    for label, state in (("Real PDF read", S.PDF_READ), ("Real extraction", S.FIELDS_EXTRACTED),
                         ("Real validation passed", S.VALIDATED),
                         ("Real template generated", S.TEMPLATE_GENERATED),
                         ("Real output saved and read back", S.SAVED)):
        n = sum(1 for r in real if reached(r, state))
        gates.append(_gate(label, "PASS" if n >= PILOT_MIN_REAL_SAVED else "UNVERIFIED",
                           "{0} real job(s) reached {1}".format(n, state)))
    controlled = [r for r in real if (r.get("controlled_email") or {}).get("status") == "OK"]
    business = [r for r in real if r["state"] == S.EMAIL_CONFIRMED]
    gates.append(_gate("Controlled email: Graph accepted (202) and found in Sent Items",
                       "PASS" if controlled or business else "UNVERIFIED",
                       "{0} controlled test send(s) confirmed; {1} business send(s) "
                       "confirmed".format(len(controlled), len(business))))
    whole = []
    for r in saved:
        names = {e["event"] for e in store.events(r["po_id"])}
        missing = [c for c in CHAIN if c not in names]
        if not missing:
            whole.append(r["po_id"])
    gates.append(_gate("Audit trail whole for a real job (discovery → saved)",
                       "PASS" if whole else "UNVERIFIED",
                       "{0} real job(s) with every event of the chain".format(len(whole))))
    audit = store.verify_audit()
    # A real-world gate: it can only PASS once real-eHub jobs are on the chain.
    gates.append(_gate("Audit trail hash chain intact (nothing altered, removed or reordered)",
                       "FAIL" if not audit["ok"] else
                       "PASS" if audit["chained"] and real else "UNVERIFIED",
                       "{0} chained event(s){1}".format(audit["chained"], "" if audit["ok"] else
                                                       "; " + str(audit["problem"]))))
    pilot = all(g["status"] == "PASS" for g in gates)

    q = quality.metrics(store)["real"]
    prod = []
    prod.append(_gate("Enough real jobs confirmed end to end",
                      "PASS" if len(business) >= PRODUCTION_MIN_REAL_CONFIRMED else "UNVERIFIED",
                      "{0} of {1} needed".format(len(business), PRODUCTION_MIN_REAL_CONFIRMED)))
    rec = q["recovery_success"]
    prod.append(_gate("Failure / recovery proven on real jobs",
                      "PASS" if rec["of"] and rec["n"] == rec["of"] else
                      "FAIL" if rec["of"] else "UNVERIFIED",
                      "{0} of {1} resumed real jobs recovered".format(rec["n"], rec["of"])))
    prod.append(_gate("Duplicate prevention observed on real work",
                      "PASS" if q["duplicates_prevented"] else "UNVERIFIED",
                      "{0} duplicate(s) blocked".format(q["duplicates_prevented"])))
    signoff = store.folder / "readiness" / "signoff.json"
    try:
        so = json.loads(signoff.read_text(encoding="utf-8"))
    except Exception:
        so = None
    prod.append(_gate("Security review and business sign-off recorded",
                      "PASS" if so and so.get("security_review") == "clean" and so.get("by")
                      else "UNVERIFIED",
                      "signed off by {0} on {1}".format(so.get("by"), so.get("at")) if so else
                      "no {0}".format(signoff)))
    production = pilot and all(g["status"] == "PASS" for g in prod)
    status = "PRODUCTION READY" if production else \
        "READY FOR CONTROLLED PILOT" if pilot else "NOT READY"
    return {"status": status, "levels": levels(store, gates, prod, so),
            "pilot_gates": gates, "production_gates": prod,
            "real_quality": q,
            "rule": "Only jobs whose document was observed in the real eHub session count."}


def _results():
    path = Path(os.environ.get("PO_TEST_RESULTS") or (HERE / "test_results.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("complete") else None


def levels(store, pilot_gates, production_gates, signoff):
    """A–F, each PASS / FAIL / UNVERIFIED with its evidence."""
    from . import mail
    data = _results()
    suites = {s["suite"]: s["status"] for s in (data or {}).get("suites") or []}

    def suites_ok(names):
        if data is None:
            return "UNVERIFIED", "no complete test run on record (python run_tests.py)"
        missing = [n for n in names if n not in suites]
        bad = [n for n in names if suites.get(n) not in (None, "OK")]
        if missing:
            return "UNVERIFIED", "not in the last run: " + ", ".join(missing)
        return ("FAIL", "failing: " + ", ".join(bad)) if bad else \
            ("PASS", "{0} green in the run of {1}".format(", ".join(names), data.get("at")))
    out = {}
    out["A_CODE_READY"] = dict(zip(("status", "evidence"), suites_ok(
        ["test_po.py", "test_po_hardening.py", "test_po_engine.py"])))
    if data is None:
        out["B_TEST_READY"] = {"status": "UNVERIFIED", "evidence": "no complete test run"}
    else:
        green = data.get("failed") == 0 and not data.get("broken")
        out["B_TEST_READY"] = {"status": "PASS" if green else "FAIL",
                               "evidence": "{0} passed, {1} failed, {2} skipped, problems: {3} "
                                           "({4})".format(data.get("passed"), data.get("failed"),
                                                          data.get("skipped"),
                                                          data.get("broken") or "none",
                                                          data.get("at"))}
    auto, why = suites_ok(["test_release.py", "test_dashboard_access.py"])
    audit = store.verify_audit()
    human = bool(signoff and signoff.get("security_review") == "clean" and signoff.get("by"))
    out["C_SECURITY_READY"] = {
        "status": "FAIL" if auto == "FAIL" or not audit["ok"] else
        "PASS" if auto == "PASS" and human else "UNVERIFIED",
        "evidence": "automated: {0}; audit chain: {1}; human security review: {2}".format(
            why, "intact" if audit["ok"] else audit["problem"],
            "recorded" if human else "NOT recorded")}
    graph_ok, missing = mail.configured()
    creds = Path(os.environ.get("PO_CREDENTIALS_FILE") or r"C:\Automation\credentials.txt")
    out_dir = store.output_dir
    writable = False
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".write-probe"
        probe.write_text("x")
        probe.unlink()
        writable = True
    except OSError:
        pass
    env = {"graph_settings": graph_ok, "recipient": bool(os.environ.get("PO_MAIL_RECIPIENT")),
           "ehub_credentials_file": creds.is_file(), "output_folder_writable": writable}
    out["D_ENVIRONMENT_READY"] = {
        "status": "PASS" if all(env.values()) else "UNVERIFIED",
        "evidence": "{0}{1}".format(", ".join("{0}: {1}".format(k, "yes" if v else "NO")
                                              for k, v in env.items()),
                                    "; Graph missing: " + ", ".join(missing) if missing else "")}
    real = [g for g in pilot_gates if g["gate"] != "Automated tests green"]
    out["E_REAL_WORLD_VERIFIED"] = {
        "status": "PASS" if real and all(g["status"] == "PASS" for g in real) else "UNVERIFIED",
        "evidence": "{0} of {1} real-world pilot gates PASS — NOT VERIFIED until the real worker "
                    "runbook (PO_WORKER_RUNBOOK.md) is executed".format(
                        sum(1 for g in real if g["status"] == "PASS"), len(real))}
    out["F_PRODUCTION_READY"] = {
        "status": "PASS" if out["E_REAL_WORLD_VERIFIED"]["status"] == "PASS" and
        all(g["status"] == "PASS" for g in production_gates) else "UNVERIFIED",
        "evidence": "{0} of {1} production gates PASS".format(
            sum(1 for g in production_gates if g["status"] == "PASS"), len(production_gates))}
    return out
