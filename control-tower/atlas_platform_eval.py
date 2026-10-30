"""
ATLAS platform eval: 44 questions (English and Arabic) asked through /api/ask
on a private server loaded with an eHub export, scored against values this
script counts from the export itself (its own code, not the hub's).

    python atlas_platform_eval.py <control-tower dir> <export.xlsx> <out dir> [label]

A question passes when its answer carries every expected value, is in the
question's language, has no unsupported number, and comes back in under 4 s.
"Unsupported number": a figure that is not a count, share or day value this
script can derive from the export (or the To Do list) — what an invented
number would be. Writes <out>/eval_<label>.json and .md. Reads only.
"""
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import urllib.request

ROOT, XLSX, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
LABEL = sys.argv[4] if len(sys.argv) > 4 else "run"
os.makedirs(OUT, exist_ok=True)
T = tempfile.mkdtemp(prefix="atlas_eval_")
os.makedirs(os.path.join(T, "inbox"))
shutil.copy(XLSX, os.path.join(T, "inbox", "data.xlsx"))
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(T, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(T, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(T, "intel"),
                   "PO_DATA_DIR": os.path.join(T, "po"), "PO_AUTO": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(T, "key")})
sys.path.insert(0, ROOT)
from dashboard import server as S, clearance as C, todo as TD  # noqa: E402

with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
B = "http://127.0.0.1:%d" % PORT
for _ in range(240):
    try:
        if json.loads(urllib.request.urlopen(B + "/api/clearance/status").read()).get("state") in ("ready", "error"):
            break
    except Exception:
        pass
    time.sleep(0.5)

# ── the truth, counted here from the export's rows ─────────────────────────
DATA = C.load()
ROWS = C.records(DATA)
ASOF = DATA["asof"][:10]
CUSTOMS = {"Customs Pre Entry", "Customs Declaration", "Duty Assessment", "Duty Payment", "Customs Release"}
WITHBU = {"Submit to BU", "Assign Clearing Agent", "Correction Required"}
AWAIT = {"Awaiting Reciept", "Awaiting Receipt"}


def is_open(r): return r["step"] != "Completed"
def late(r): return is_open(r) and isinstance(r["overdue"], (int, float)) and r["overdue"] > 0
def transit(r): return is_open(r) and not r["ata"] and r["eta"] and r["eta"][:10] >= ASOF


def stage(r):
    if not is_open(r): return "done"
    if transit(r): return "transit"
    if r["step"] in AWAIT: return "awaiting"
    if r["step"] in CUSTOMS: return "customs"
    if r["step"] in WITHBU: return "arrived"
    return "other"


def sel(bu=None): return [r for r in ROWS if bu is None or r["bu"] == bu]
def cnt(rs, f): return sum(1 for r in rs if f(r))
def st(s, bu=None): return cnt(sel(bu), lambda r: stage(r) == s)
def pd(bu=None): return cnt(sel(bu), late)
def op(bu=None): return cnt(sel(bu), is_open)
def ontime(bu=None): return cnt(sel(bu), lambda r: r["ontime"])
def pct(bu=None): return round(100.0 * ontime(bu) / len(sel(bu)), 1)


per_bu = {}
for r in ROWS:
    if late(r): per_bu[r["bu"]] = per_bu.get(r["bu"], 0) + 1
TOP = sorted(per_bu.items(), key=lambda kv: (-kv[1], kv[0]))
TODO = TD.build(lambda p: True, None)

# Every figure the export (or To Do) supports: counts, shares and day values.
SUPPORTED = set()
groups = [None] + sorted({r["bu"] for r in ROWS if r["bu"]})
for bu in groups:
    rs = sel(bu)
    subsets = {"all": rs, "open": [r for r in rs if is_open(r)], "late": [r for r in rs if late(r)],
               "done": [r for r in rs if not is_open(r)], "notontime": [r for r in rs if not r["ontime"]]}
    for s_ in ("transit", "arrived", "customs", "awaiting", "done", "other"):
        subsets[s_] = [r for r in rs if stage(r) == s_]
    by_step = {}
    for r in subsets["late"]:
        by_step[r["step"]] = by_step.get(r["step"], 0) + 1
    SUPPORTED |= set(by_step.values())
    for k, sub in subsets.items():
        n, on = len(sub), cnt(sub, lambda r: r["ontime"])
        SUPPORTED |= {n, on, n - on}
        if n:
            SUPPORTED |= {round(100.0 * on / n), round(100.0 * on / n, 1), round(100.0 * (n - on) / n),
                          round(100.0 * n / max(1, len(rs))), round(100.0 * n / max(1, len(rs)), 1)}
SUPPORTED |= {TODO["outstanding"], TODO["count"]} | set(TODO["by_state"].values())
for i in TODO["items"]:
    SUPPORTED |= {int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", i["title"] + " " + (i.get("detail") or ""))}
SUPPORTED |= {95, 0}                         # the export's on-time target, and none
for r in ROWS:                               # a named shipment's own day values
    if isinstance(r["overdue"], (int, float)):
        SUPPORTED |= {int(r["overdue"]), round(r["overdue"], 1)}
REF_PARTS = {p for r in ROWS for p in re.findall(r"\d+", r["awb"] or "")}     # pieces of AWB/BOL numbers


def refs_support(q):
    out = set()
    for a in C.awbs_in(q, DATA):
        for r in C.find(a, DATA):
            for v in (r["overdue"],):
                if isinstance(v, (int, float)):
                    out |= {int(v), round(v, 1)}
    return out


# ── the questions ──────────────────────────────────────────────────────────
def f(n): return "{0:,}".format(n)


NODATA = r"no data|don't have|isn't connected|not connected|no (freight|rate|tender|document)|لا توجد بيانات|غير متصلة"
EG, GH, NG = "EG", "GH", "NG"
REF1 = next(r for r in ROWS if late(r) and r["mode"] == "Air")
REF2 = next(r for r in ROWS if late(r) and r["mode"] != "Air" and r["awb"] != REF1["awb"])
Q = [
    # counts (English)
    ("How many shipments are past due?", {}, [f(pd()), f(op())], "en"),
    ("How many shipments are in transit?", {}, [f(st("transit"))], "en"),
    ("How many shipments are at customs?", {}, [f(st("customs"))], "en"),
    ("How many shipments are cleared and awaiting delivery?", {}, [f(st("awaiting"))], "en"),
    ("How many shipments have arrived and are with the BU?", {}, [f(st("arrived"))], "en"),
    ("How many shipments are completed?", {}, [f(st("done"))], "en"),
    ("Which BU has the most past due shipments?", {}, [TOP[0][0], f(TOP[0][1])], "en"),
    ("How many past due shipments in GH?", {}, [f(pd(GH))], "en"),
    ("How many shipments are past due in Nigeria?", {}, [f(pd(NG))], "en"),
    ("How many shipments does JR have?", {}, [f(len(sel("JR")))], "en"),
    ("What is the on-time rate?", {}, [str(pct()), f(ontime())], "en"),
    ("What is the on-time rate for EG?", {}, [str(pct(EG))], "en"),
    ("How many shipments are in transit in EG?", {}, [f(st("transit", EG))], "en"),
    ("How many EG shipments are at customs?", {}, [f(st("customs", EG))], "en"),
    ("How many shipments are there in total?", {}, [f(len(ROWS))], "en"),
    ("How many open shipments does GH have?", {}, [f(op(GH))], "en"),
    # what a number means
    ("How is past due counted?", {}, ["Performance"], "en"),
    ("What does 'Cleared, awaiting delivery' mean?", {}, ["Awaiting Receipt"], "en"),
    ("How is in transit calculated?", {}, ["ETA"], "en"),
    ("What does at customs mean?", {}, ["Declaration"], "en"),
    ("How is the on-time rate calculated?", {}, ["On Time flag"], "en"),
    ("Explain this number", {"page": "hub"}, [f(st("transit")), f(st("customs")), f(st("awaiting"))], "en"),
    ("Explain this number", {"page": "hub", "view": {"stage": "pastdue"}}, ["Performance", f(pd())], "en"),
    # no data: said plainly
    ("What are our freight rates to Lagos?", {}, ["@nodata"], "en"),
    ("Show me this week's spot tenders", {}, ["@nodata"], "en"),
    ("Which documents are missing for EG shipments?", {}, ["@nodata"], "en"),
    ("What is the freight rate from Antwerp to Mombasa?", {}, ["@nodata"], "en"),
    # To Do and the run
    ("What is on my To Do list?", {}, [f(TODO["outstanding"])], "en"),
    ("What needs my attention in To Do?", {}, [f(TODO["outstanding"])], "en"),
    ("Is the ATA run running right now?", {"page": "overview"}, ["@running"], "en"),
    # one shipment
    ("What is the status of {0}?".format(REF1["awb"]), {}, [REF1["step"], REF1["bu"]], "en"),
    ("Why is {0} late?".format(REF2["awb"]), {}, [str(int(REF2["overdue"]))], "en"),
    # Arabic
    ("كم شحنة متأخرة في مصر؟", {}, [f(pd(EG))], "ar"),
    ("كم شحنة في الطريق؟", {}, [f(st("transit"))], "ar"),
    ("كم شحنة في الجمارك؟", {}, [f(st("customs"))], "ar"),
    ("كم شحنة بانتظار التسليم؟", {}, [f(st("awaiting"))], "ar"),
    ("ما هي نسبة الالتزام بالموعد؟", {}, [str(pct())], "ar"),
    ("كيف يتم حساب الشحنات المتأخرة؟", {}, ["Performance"], "ar"),
    ("ما هي أسعار الشحن إلى لاغوس؟", {}, ["@nodata"], "ar"),
    ("ما هي المهام في قائمة المهام؟", {}, [f(TODO["outstanding"])], "ar"),
    ("كم شحنة لدى غانا؟", {}, [f(len(sel(GH)))], "ar"),
    ("أي وحدة لديها أكثر الشحنات المتأخرة؟", {}, [TOP[0][0], f(TOP[0][1])], "ar"),
    ("كم شحنة متأخرة في نيجيريا؟", {}, [f(pd(NG))], "ar"),
    ("ما معنى تم التخليص بانتظار التسليم؟", {}, ["Awaiting Receipt"], "ar"),
]

# Held out: paraphrases written after the tools, never tuned on; scored apart.
HELD = [
    ("how many shipments are overdue right now", {}, [f(pd())], "en"),
    ("number of late shipments in Ghana", {}, [f(pd(GH))], "en"),
    ("what's our on time percentage", {}, [str(pct())], "en"),
    ("count of shipments waiting for delivery", {}, [f(st("awaiting"))], "en"),
    ("how many are stuck in customs", {}, [f(st("customs"))], "en"),
    ("how many cleared but not delivered", {}, [f(st("awaiting"))], "en"),
    ("which business unit is the most behind", {}, [TOP[0][0], f(TOP[0][1])], "en"),
    ("كم عدد الشحنات المتأخرة", {}, [f(pd())], "ar"),
    ("كم شحنة اكتملت", {}, [f(st("done"))], "ar"),
    ("what does past due mean", {}, ["Performance"], "en"),
    ("how many shipments in total for Kenya", {}, [f(len(sel("KE")))], "en"),
    ("do we have tender prices for next week", {}, ["@nodata"], "en"),
]
Q = [q + ("dev",) for q in Q] + [q + ("held",) for q in HELD]

NUM = re.compile(r"(?<![\w/#=.:-])\d[\d,]*(?:\.\d+)?(?![\w-])")


def unsupported(ans, q, extra):
    plain = re.sub(r"\]\([^)]*\)", "]", ans)
    plain = re.sub(r"\b\d{4}-\d{2}-\d{2}(T[\d:]+)?\b|\b\d{1,2} [A-Z][a-z]{2} \d{4}\b|\b\d{1,2}:\d{2}\b", " ", plain)
    for a in C.awbs_in(q + " " + ans, DATA):          # references are names, not figures
        plain = plain.replace(a, " ")
    allowed = {str(x) for x in SUPPORTED | extra} | set(NUM.findall(q)) | REF_PARTS | \
        {str(y) for y in range(2000, 2100)}
    return [m for m in NUM.findall(plain) if m.replace(",", "") not in allowed and m not in allowed]


results = []
for q, ctx, must, lang, part in Q:
    body = json.dumps({"question": q, "context": ctx}).encode()
    req = urllib.request.Request(B + "/api/ask", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Origin": B})
    a = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
    except Exception as e:
        d = {"answer": "ERROR " + str(e)}
    ms = round((time.perf_counter() - a) * 1000)
    ans = str(d.get("answer") or "")
    missing = []
    for m in must:
        if m == "@nodata":
            ok = re.search(NODATA, ans, re.I) is not None
        elif m == "@running":
            ok = re.search(r"running|تعمل", ans, re.I) is not None
        else:
            ok = m in ans or m.replace(",", "") in ans
        if not ok:
            missing.append(m)
    bad = unsupported(ans, q, refs_support(q))
    if "@nodata" in must:
        bad = NUM.findall(re.sub(r"\]\([^)]*\)", "]", ans))     # a no-data answer gives no figure at all
    lang_ok = (re.search(r"[؀-ۿ]", ans) is not None) if lang == "ar" else True
    passed = not missing and not bad and lang_ok and ms < 4000
    results.append({"part": part, "q": q, "context": ctx, "expected": must, "missing": missing, "unsupported": bad,
                    "language_ok": lang_ok, "ms": ms, "pass": passed, "answer": ans[:900],
                    "intent": d.get("intent"), "tools": d.get("tools")})

held = [r for r in results if r["part"] == "held"]
results_all, results = results, [r for r in results if r["part"] == "dev"]
n = len(results)
summary = {"label": LABEL, "questions": n, "passed": sum(r["pass"] for r in results),
           "held_out": len(held), "held_out_passed": sum(r["pass"] for r in held),
           "held_out_no_invented_numbers": sum(not r["unsupported"] for r in held),
           "no_invented_numbers": sum(not r["unsupported"] for r in results),
           "language_ok": sum(r["language_ok"] for r in results),
           "latency_ms_max": max(r["ms"] for r in results),
           "latency_ms_p95": sorted(r["ms"] for r in results)[int(0.95 * (n - 1))],
           "asof": ASOF}
json.dump({"summary": summary, "results": results_all}, open(os.path.join(OUT, "eval_%s.json" % LABEL), "w"),
          indent=1, ensure_ascii=False)
with open(os.path.join(OUT, "eval_%s.md" % LABEL), "w", encoding="utf-8") as fh:
    fh.write("# ATLAS platform eval — {label}\n\nExport data as of {asof}. {passed}/{questions} passed; "
             "{no_invented_numbers}/{questions} with no unsupported number; slowest {latency_ms_max} ms.\n\n".format(**summary))
    fh.write("| # | Question | Pass | Missing | Unsupported | ms |\n|---|---|---|---|---|---|\n")
    for i, r in enumerate(results_all, 1):
        fh.write("| {0} | {1} | {2} | {3} | {4} | {5} |\n".format(
            i, ("(held out) " if r["part"] == "held" else "") + r["q"].replace("|", "/"), "yes" if r["pass"] else "**no**", ", ".join(r["missing"]),
            ", ".join(r["unsupported"]), r["ms"]))
print(json.dumps(summary))
for i, r in enumerate(results_all, 1):
    if not r["pass"]:
        print("FAIL %d %s | missing=%s unsupported=%s lang=%s ms=%d\n   %s" % (
            i, r["q"], r["missing"], r["unsupported"], r["language_ok"], r["ms"], r["answer"][:240].replace("\n", " ")))
os._exit(0)
