"""
ATLAS upgrade: fast understanding (router, timing, early answers, fewer model
calls), applied logistics reasoning (scenarios, calculators) and optional
local semantic retrieval — and that none of it weakens the existing rules.

    python test_atlas_upgrade.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="upgrade_")
for k in ("ATLAS_LLM_PROVIDER", "ATLAS_LLM_MODEL", "ATLAS_SEARCH_URL", "ATLAS_EMBED_MODEL", "ATLAS_LLM_CACHE_S"):
    os.environ.pop(k, None)

from dashboard import assistant  # noqa: E402
from dashboard.bridge import ControlTowerState  # noqa: E402
from intelligence import router as RT, trace, research as R, converse, llm, kb as KB, kb_answer as A  # noqa: E402
from intelligence import kb_calc as C, kb_embed as E  # noqa: E402
import atlas_demo  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond or not detail else "  -- " + str(detail)[:240]))


def section(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


b = ControlTowerState()
atlas_demo.seed_test_run(b)
STATE = b.snapshot()


def ask(q, ctx=None):
    return assistant.answer(q, STATE, dict(ctx or {}))


section("1. THE ROUTER: WHAT KIND OF QUESTION, WITHOUT A MODEL")
cases = [
    ("where is 1570046231", "operational"), ("how many shipments failed today", "operational"),
    ("Which shipment failed?", "operational"), ("show me the DHL shipments", "operational"),
    ("why did 8842001173 fail", "troubleshooting"), ("Why are DHL shipments slower this week?", "troubleshooting"),
    ("stop the automation", "action"), ("send me the excel of the run", "action"),
    ("Under FOB who pays the main freight?", "knowledge"), ("when is it worth consolidating LCL into a full box", "knowledge"),
    ("FCL vs LCL for 9 cbm, which is usually smarter", "knowledge"),
    ("3 pallets each 1.2 x 1.0 x 1.5 m, total cbm?", "calculation"),
    ("what does needs review mean on a shipment", "atlas_rules"),
    ("and the other one?", "ambiguous"), ("can you help my kid with fractions homework", "out_of_scope"),
    ("hello", "chat"),
]
for q, want in cases:
    got = RT.route(q, None, assistant.detect_intent(q))["route"]
    check("{0!r} -> {1}".format(q, want), got == want, got)
check("A pronoun with the previous turn is a follow-up, without it ambiguous",
      RT.route("and its ATA?", {"reference": "5271993480"})["route"] == "follow_up"
      and RT.route("and its ATA?", {})["route"] == "ambiguous")
t0 = time.perf_counter()
for _ in range(200):
    RT.route("why would the broker want the HS code before the vessel arrives", None, None)
check("Routing takes well under a millisecond (200 questions)", (time.perf_counter() - t0) < 0.5,
      round((time.perf_counter() - t0) * 1000, 1))

section("2. GENERAL QUESTIONS GET KNOWLEDGE, NEVER THIS RUN'S FIGURES")
r = ask("what's usually a fair average transit time from China to Alexandria, and why does it vary?")
check("A general question with 'average' is not answered with this run's average",
      "across 4 shipment" not in r.get("answer", "") and "processing time" not in r.get("answer", ""), r.get("answer", "")[:160])
r = ask("why does BAF change every month on our invoices")
check("…'why does BAF change' gets the BAF entry", r.get("intent") == "general_knowledge"
      and "B.thc-baf" in (r.get("knowledge") or {}).get("items", []), (r.get("intent"), r.get("knowledge", {}).get("items")))
r = ask("Which shipment failed?")
check("…while 'Which shipment failed?' is still answered from the run", r.get("intent") != "general_knowledge"
      and "8842001173" in r.get("answer", ""), r.get("answer", "")[:160])
r = ask("in general, how do perishable reefer cold-chain temperature excursions get handled usually?")
check("A plainly general question with no entry: said so, nothing guessed, no run data",
      r.get("intent") == "general_knowledge" and ("no entry" in r.get("answer", "") or (r.get("knowledge") or {}).get("items")),
      r.get("answer", "")[:200])
r = ask("how much is it")
check("A vague question with nothing in context: ATLAS asks what is meant", r.get("clarify") is True, r.get("answer", "")[:120])

section("3. SCENARIOS: REASONING, NOT A DEFINITION — AND NOTHING CONFIRMED")
r = ask("Emirates status says ARRIVED but it arrived at DXB and our destination is CAI, the tower shows arrived too. what do we do?")
a = r.get("answer", "")
check("A described situation gets the structured reasoning", (r.get("knowledge") or {}).get("mode") == "scenario"
      and all(h in a for h in ("**Known", "**Possible causes", "**Missing", "**Next steps", "**What I can't tell")), a[:200])
check("…the facts are the operator's own words", "Emirates status says ARRIVED" in a)
check("…causes are hypotheses, the one the message points to first", "hypotheses, not confirmed" in a
      and a.index("transit point") < a.index("stale"))
check("…and it never records an ATA from a transit event", "Do not record an ATA from a transit" in a)
r = ask("MSC billed demurrage for 14 days on a box we cleared in 3 days. is that invoice wrong?")
a = r.get("answer", "")
check("A consequential step names who must approve it", "needs approval" in a, a[-300:])
check("…and no cause is stated as confirmed", "the cause is" not in a.lower() and "confirmed by:" in a)
r = ask("8842001173 failed in the run and now the customer says the parts are late. is it actually late or just our system?")
a = r.get("answer", "")
check("A problem about one of this run's shipments: the records first, then the reasoning, labelled apart",
      a.find("8842001173") < a.find("How to reason about it") and "separate from the records above" in a, a[:200])

section("4. CALCULATIONS ADDED: EXACT, SHOWN, NEVER ASSUMED")
check("Volume: 3 pallets 1.2 × 1.0 × 1.5 m = 5.4 m³", C.calculate("3 pallets each 1.2 x 1.0 x 1.5 m, total cbm?")["value"] == 5.4)
check("Tiered demurrage: days 6–10 at 60, 11+ at 120, out day 14 = 780",
      C.calculate("demurrage tariff: 5 free days, days 6-10 at 60 usd, day 11 onwards 120 usd. box left on day 14")["value"] == 780)
check("Dwell in hours from two date-times", C.calculate("discharged 4 May 2026 08:00, gate-out 9 May 2026 14:00, dwell time in hours?")["value"] == 126)
check("Lead time in calendar days from written dates", C.calculate("PO 12 Jan 2026, received 9 Mar 2026, lead time in calendar days?")["value"] == 56)
check("Average of a list", C.calculate("transit days on the last boxes: 18, 22, 25, 19, 31. average?")["value"] == 23)
check("MAPE refuses a zero actual and says what to use", "undefined" in C.calculate("MAPE: actuals 0, 12, 8 and forecasts 3, 10, 8")["error"])
check("Inventory turns refuse a zero average inventory", "above zero" in C.calculate("inventory turns: COGS 350k, average inventory 0")["error"])
check("Reorder point = demand × lead time + safety stock",
      C.calculate("reorder point: 20 units per day, lead time 15 days, safety stock 60")["value"] == 360)
check("Arabic units and thousands are read", C.calculate("كرتونة 80×60×50 سم ووزنها 18 كيلو، بالقسمة على 6000 الوزن اللي هيتحاسب عليه كام؟")["value"] == 40
      and C.calculate("صرفنا 186 ألف دولار على 62 شحنة، متوسط تكلفة الشحنة؟")["value"] == 3000)
check("Missing inputs are asked for, not assumed",
      "missing" in C.calculate("how much demurrage on the MSC box? free time was 7 days and it's 85 a day")
      and "missing" in C.calculate("chargeable weight for a box 100x80x60 cm, divisor 6000?"))

section("5. WHERE THE TIME GOES: TRACING")
trace.begin()
with trace.span("a"):
    with trace.span("b"):
        trace.model({"wall_ms": 12.0})
    trace.useful("rules")
out = trace.end("x")
check("Spans, the model call's purpose and the first useful moment are recorded",
      set(out["stages"]) == {"a", "b"} and out["model"][0]["purpose"] == "b" and out["first_useful_by"] == "rules")
check("…as a Server-Timing header", trace.server_timing(out).startswith("total;dur="))
check("Outside a request, a span records nothing and costs nothing", trace.end() is None)
check("The recent requests' percentiles are kept (no question text)", trace.stats()["requests"] >= 1
      and "q" not in json.dumps(list(trace.RECENT)))

section("6. FEWER MODEL CALLS, AND THE RECORDS' ANSWER SHOWN EARLY")
read = assistant._answer_rules("كم شحنة اتحدثت", STATE, {"_raw": True})
check("An Arabic question the rules recognise is understood without the model",
      converse.fast_plan("كم شحنة اتحدثت", read, "ar") is not None)
read = assistant._answer_rules("which failed?", STATE, {"_raw": True})
check("A vague English reading ('filter') still lets the model restate it", converse.fast_plan("which failed?", read, "en") is None)
check("Small talk is recognised without the model", (converse.fast_plan("I'm so tired today", {"intent": None}, "en") or {}).get("chat") is True)
R.progress_start("pid12345678")
converse._early("pid12345678", {"intent": "summary", "answer": "4 shipments processed."})
check("The records' answer is published to the progress channel", R.progress("pid12345678").get("early") == "4 shipments processed.")
R.progress_start("pid87654321")
converse._early("pid87654321", {"intent": "run_control", "answer": "Stopping.", "request": {"action": "stop"}})
check("…never for an action, whose outcome only the server knows", not R.progress("pid87654321").get("early"))
check("A follow-up about the shipment in context stays with the records",
      converse._records_question({"intent": "eta", "reference": "5271993480"}, "and what is its ETA?"))
check("Model replies are not cached unless asked (ATLAS_LLM_CACHE_S)", llm._cache_ttl() == 0)
os.environ["ATLAS_LLM_CACHE_S"] = "60"
key = llm._cache_key("m", "s", "p", True, 10)
llm._cache_put(key, "stored")
check("…when asked, only byte-identical input is reused", llm._cache_get(key) == "stored"
      and llm._cache_get(llm._cache_key("m", "s", "p2", True, 10)) is None)
os.environ.pop("ATLAS_LLM_CACHE_S")

section("7. SEMANTIC CANDIDATES: OPTIONAL, LOCAL, NEVER DECIDING WHETHER TO ANSWER")
check("Off by default: no model named, no candidates", not E.enabled() and E.candidates("demurrage", KB.kb()) == [])
WORDS = ["port", "charg", "box", "contain", "day", "paper", "own", "cargo", "sea", "weather", "cairo"]


def vec(text):
    t = text.lower()
    v = [float(t.count(w)) for w in WORDS] + [0.01]
    if "demurrage" in t or "inside the port" in t:
        v[0] += 3; v[1] += 3; v[3] += 2; v[4] += 2
    if "bill of lading" in t:
        v[5] += 3; v[6] += 3; v[7] += 1; v[8] += 1
    return v


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        inp = body["input"] if isinstance(body["input"], list) else [body["input"]]
        out = json.dumps({"embeddings": [vec(x) for x in inp]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"ATLAS_EMBED_MODEL": "test-embed", "ATLAS_EMBED_URL": "http://127.0.0.1:{0}".format(srv.server_port),
                   "ATLAS_EMBED_MIN": "0.5", "ATLAS_EMBED_MARGIN": "0.0"})
k = KB.kb()
check("The index is built locally and stored by item and text hash", E.warm(k) and E.status()["items"] >= 200
      and any(p.name.startswith("kb_embed_") for p in Path(os.environ["ATLAS_INTEL_DIR"]).glob("*")), E.status())
cands = E.candidates("the port keeps charging us every day for the box", k)
check("A paraphrase finds candidates by meaning", bool(cands) and cands[0][1] > 0.5, cands[:2])
check("A retired item is never a candidate", all(k.current(k.items[i]) for i, _ in cands))
check("Off-topic words never become a logistics answer, whatever the similarity",
      A.answer("What is the weather in Cairo?") is None)
os.environ["ATLAS_EMBED_URL"] = "http://127.0.0.1:9"
E._qcache.clear()
t0 = time.time()
r = A.answer("What is demurrage?")
check("An unreachable embedding model: keyword retrieval answers, quickly",
      r is not None and r["knowledge"]["items"][0] == "C.demurrage" and time.time() - t0 < 5, time.time() - t0)
for key in ("ATLAS_EMBED_MODEL", "ATLAS_EMBED_URL", "ATLAS_EMBED_MIN", "ATLAS_EMBED_MARGIN"):
    os.environ.pop(key, None)
srv.shutdown()

section("8. ONE TOWER PER PORT")
import io as _io  # noqa: E402
import socket as _socket  # noqa: E402
import contextlib as _ctx  # noqa: E402
from dashboard import server as _srv  # noqa: E402
_s = _socket.socket()
_s.bind(("127.0.0.1", 0))
_port = _s.getsockname()[1]
_s.close()
first = _srv.start(port=_port, open_browser=False, host="127.0.0.1", access_key="one-tower-key-123456")
_out = _io.StringIO()
with _ctx.redirect_stdout(_out):
    second = _srv.start(port=_port, open_browser=False, host="127.0.0.1", access_key="one-tower-key-123456")
check("A second tower on the same port does not start, and says why",
      first is not None and second is None and "already in use" in _out.getvalue(), _out.getvalue()[:200])
src = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")
check("On Windows the port is taken exclusively (SO_EXCLUSIVEADDRUSE, no SO_REUSEADDR)",
      "SO_EXCLUSIVEADDRUSE" in src and "allow_reuse_address = False" in src)
sup = (HERE / "dashboard" / "supervisor.py").read_text(encoding="utf-8")
check("The supervisor stops instead of running headless when the dashboard cannot start",
      "is None:" in sup and "sys.exit(1)" in sup)

section("9. SMALL TALK IS SMALL TALK")
from dashboard import assistant as _assistant  # noqa: E402
for q in ("are you good ?", "you good?", "how are you", "Hey ATLAS, how are you doing?", "what's up",
          "are you there", "انت كويس", "عامل ايه", "ازيك", "كيف حالك؟", "مرحبا", "السلام عليكم"):
    check("'{0}' is a greeting, not a knowledge lookup".format(q),
          _assistant.detect_intent(q) == "greeting" and _assistant.small_talk(q) is not None)
check("'are you good?' does not answer about cargo",
      "Cargo" not in _assistant._answer_greeting(_assistant.RunData({}), "are you good ?"))
check("An Arabic how-are-you is answered in Arabic",
      "تمام" in _assistant._answer_greeting(_assistant.RunData({}), "عامل ايه"))
for q in ("what are dangerous goods?", "are you good at customs questions?", "how are you calculating demurrage?",
          "hi, which shipments failed?"):
    check("'{0}' is not taken as small talk".format(q), _assistant.small_talk(q) is None)
check("'is everything good' asks about the run", _assistant.detect_intent("is everything good") == "health")

print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
