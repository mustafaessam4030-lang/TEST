"""
ATLAS self-healing for PO jobs (po/heal.py). TEST DATA only, sanitised fixtures.

    python test_po_heal.py

A stopped job is tested again in isolation, from its own kept PDF, through
the real pipeline with email off. A fix is VERIFIED only when the sandbox job
passes every validation check and gets its template; only then is it learned.
The job itself never changes state, gets no output and sends nothing; the
safety gate cannot be passed by a test.
"""

import glob
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "fixtures")]
WORK = Path(tempfile.mkdtemp(prefix="ct_heal_"))
os.environ["PO_DATA_DIR"] = str(WORK / "default")
os.environ["PO_OUTPUT_DIR"] = str(WORK / "out")

import po_engine as F                                       # noqa: E402
from icums_boe import icums_boe_pdf                         # noqa: E402
from po import doctypes, extract as X, heal as H, pipeline as P, service, store as S  # noqa

PASS, FAIL = [], []
CONFIG = {"recipient": "someone@example.test", "sender": None, "auto_send": True,
          "defaults": {"supplier": None, "branch": None, "charge_to": None, "priority": None},
          "confirm_wait_s": 0, "email_disabled": False}
RED = (0.8, 0.0, 0.0)


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(str(detail)[:400]) if detail and not ok else ""))


_n = {"i": 0}


def job(data, identifier="40726534505", old_reader=False):
    """A job run as it was: old_reader = the reader before stamps were set aside."""
    _n["i"] += 1
    st = S.Store(folder=WORK / "job{0}".format(_n["i"]), output_dir=WORK / "o{0}".format(_n["i"]))
    rec = st.create(doctypes.DEFAULT, "J552493", {})
    keep = X._overprint
    if old_reader:
        X._overprint = lambda w, ref: None
    try:
        cfg = dict(CONFIG, email_disabled=True, auto_send=False)
        rec = P.process(st, rec, F.Source(data, ref="J552493", identifier=identifier), cfg,
                        sleep=lambda s: None)
    finally:
        X._overprint = keep
    return st, rec


def sandboxes():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "atlas_heal_*")))


print("=" * 74 + "\n1. A FIX THAT WORKS: TESTED IN ISOLATION, VERIFIED, LEARNED — THE JOB UNTOUCHED\n" +
      "=" * 74)
st, rec = job(icums_boe_pdf(stamp={"size": 6.5, "color": RED}), old_reader=True)
check("Reproduced: the old reader stops on the stamped tax table (EXTRACTION_FAILED)",
      rec["state"] == S.EXTRACTION_FAILED, rec["state"])
before_dirs = sandboxes()
events_before = len(st.events(rec["po_id"]))
a = H.heal(st, rec, CONFIG)
check("ATLAS tests the kept PDF with the current reader: VERIFIED (validation passed, template "
      "made in the sandbox)", a["outcome"] == H.VERIFIED and a["validation_passed"] and
      a["template"] and a["sandbox_state"] == S.SAVED, a)
check("...with its evidence: the document hash and the stamp words set aside",
      a["evidence"]["document_sha256"] == rec["document"]["sha256"] and
      "Validated" in a["evidence"]["set_aside"], a.get("evidence"))
job_now = st.get(rec["po_id"])
check("The job keeps its state, gets no output and no email (recovery is a person's re-run)",
      job_now["state"] == S.EXTRACTION_FAILED and not job_now.get("output") and
      not job_now.get("email"), (job_now["state"], job_now.get("output")))
check("The sandbox is deleted after the test", sandboxes() == before_dirs)
ev = [e for e in st.events(rec["po_id"])][events_before:]
check("The test is on the job's audit trail (HEAL_TESTED, VERIFIED)",
      [e["event"] for e in ev] == ["HEAL_TESTED"] and ev[0]["status"] == "VERIFIED", ev)
cat = H.catalog(st)[H.category(job_now)]
check("Learned only after verification: the catalog entry is 'verified', with cause, strategy, "
      "reader version, evidence and limitations",
      cat["status"] == "verified" and cat["reader"] == H.reader_version() and cat["cause"] and
      cat["strategy"] and cat["limitations"] and cat["verification"]["validation_passed"], cat)
check("...and a known fix is matched for the same failure with the same reader",
      H.known_fix(st, job_now) is not None)
check("The dashboard row says it plainly — not a green success",
      service.card(job_now)["atlas_healing"].startswith("ATLAS tested a fix on the kept PDF: "
                                                        "verified — re-run the job"))
again = H.heal(st, st.get(rec["po_id"]), CONFIG)
check("The same strategy is never repeated: a second test with this reader is NOT_TESTED",
      again["outcome"] == H.NOT_TESTED and "already tested" in again["why"], again)
try:
    st.transition(st.get(rec["po_id"]), S.TEMPLATE_GENERATED, "bypass attempt")
    bypass = "allowed"
except Exception as error:
    bypass = type(error).__name__
check("...a direct jump to TEMPLATE_GENERATED is refused by the store ({0})".format(bypass),
      bypass != "allowed")

print("=" * 74 + "\n2. NO FIX: STILL BLOCKED, NOTHING LEARNED, A PERSON DECIDES\n" + "=" * 74)
st2, rec2 = job(icums_boe_pdf(stamp={"size": 6.5}))
b = H.heal(st2, rec2, CONFIG)
check("A stamp printed exactly like the table: STILL_BLOCKED, with the reason",
      b["outcome"] == H.STILL_BLOCKED and "Validated" in b["why"] and "tax_lines" in b["blocking"],
      b)
check("...nothing is learned as a fix", H.catalog(st2)[b["category"]]["status"] == "unverified"
      and H.known_fix(st2, st2.get(rec2["po_id"])) is None)
check("...the row says a person is needed",
      "still blocked — needs a person" in service.card(st2.get(rec2["po_id"]))["atlas_healing"])
st3, rec3 = job(icums_boe_pdf(continuation_number="40726534505 / 01"))
c = H.heal(st3, rec3, CONFIG)
check("Two different suffixes (/ 00, / 01): never 'healed' — no output anywhere ({0})".format(
      rec3["state"]), c["outcome"] != H.VERIFIED and not st3.get(rec3["po_id"]).get("output"), c)

print("=" * 74 + "\n3. BOUNDS AND FAILURES OF THE TEST ITSELF\n" + "=" * 74)
st4, rec4 = job(icums_boe_pdf(stamp={"size": 6.5}))
(Path(st4.folder) / "documents" / (rec4["document"]["sha256"] + ".pdf")).unlink()
d = H.heal(st4, rec4, CONFIG)
check("No kept PDF: STILL_BLOCKED, escalated — nothing guessed", d["outcome"] == H.STILL_BLOCKED
      and "no kept PDF" in d["why"], d)
st5, rec5 = job(icums_boe_pdf(stamp={"size": 6.5}))
keep = P.process
P.process = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("service down"))
try:
    e = H.heal(st5, rec5, CONFIG)
finally:
    P.process = keep
check("The test itself fails (an exception): STILL_BLOCKED, the error recorded, no sandbox left",
      e["outcome"] == H.STILL_BLOCKED and "service down" in e["why"] and
      not (sandboxes() - before_dirs), e)
st6, rec6 = job(icums_boe_pdf(stamp={"size": 6.5}))
rec6["healing"] = [{"reader": "older{0}".format(i), "outcome": "STILL_BLOCKED"} for i in range(3)]
st6.save(rec6)
f = H.heal(st6, st6.get(rec6["po_id"]), CONFIG)
check("At most {0} tests per job: then NOT_TESTED".format(H.MAX_TESTS),
      f["outcome"] == H.NOT_TESTED and "limit" in f["why"], f)
st7, rec7 = job(icums_boe_pdf(stamp={"size": 6.5}))
H.disable(st7, H.category(rec7), by="TEST", reason="faulty strategy")
g = H.heal(st7, rec7, CONFIG)
check("A disabled strategy is not run (rollback), and the disable is in the history",
      g["outcome"] == H.NOT_TESTED and "disabled" in g["why"] and
      H.catalog(st7)[H.category(rec7)]["history"][-1]["event"] == "disabled", g)
st8, rec8 = job(icums_boe_pdf())
h = H.heal(st8, rec8, CONFIG)
check("A job that did not stop is not tested, and nothing is recorded on it",
      rec8["state"] == S.SAVED and h["outcome"] == H.NOT_TESTED and
      not st8.get(rec8["po_id"]).get("healing"), (rec8["state"], h))
check("after_stop() never raises, whatever it is given",
      H.after_stop(st8, None) is None and H.after_stop(st8, {"state": "X"}) is None)

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
