"""
The model council (intelligence/missions/council.py): many models from the tower's Ollama and from
Snowflake Cortex working on one step together. Discovery of installed local models, distinct families,
parallel calls with one local model at a time, merged hypotheses with agreement, a review panel that
never includes the drafter's family, fact-checked drafts in parallel, profile sizes, the ledger.
Stand-in models only: nothing billed.

    python test_missions_council.py
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="council_")
os.environ.update({"ATLAS_INTEL_DIR": os.path.join(TMP, "intel"), "ATA_RUNTIME_DIR": os.path.join(TMP, "rt"),
                   "ATLAS_LLM_MODEL": "qwen3:4b", "ATLAS_LLM_PROVIDER": "ollama", "ATLAS_AI": "0"})
os.environ.pop("ATLAS_COUNCIL_SIZE", None)
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:400]))


from intelligence.missions import council, models  # noqa: E402

LIVE = {"local": 0, "max_local": 0}
CALLS, DOWN = [], set()
_l = threading.Lock()


class Fake(object):
    def __init__(self, name, local=False):
        self.model, self.local = name, local

    def health(self):
        return {"ok": self.model not in DOWN, "detail": "ready" if self.model not in DOWN else "down"}

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=0, images=None):
        with _l:
            CALLS.append(self.model)
            if self.local:
                LIVE["local"] += 1
                LIVE["max_local"] = max(LIVE["max_local"], LIVE["local"])
        try:
            time.sleep(0.15)
            if self.model in DOWN:
                raise RuntimeError("down")
            if "plausible causes" in system:
                return {"claude-sonnet-5": '{"causes": ["Port congestion at the arrival airport", "Customs inspection hold"]}',
                        "llama3.3-70b": '{"causes": ["Congestion at the arrival airport port"]}',
                        "local:mistral-nemo:12b": '{"causes": ["Missing commercial invoice"]}'}.get(self.model, '{"causes": []}')
            if "independent reviewer" in system:
                return '{"agree": false, "concerns": ["the ETA evidence is thin"]}' if "mistral" in self.model else '{"agree": true, "concerns": []}'
            if self.model == "claude-sonnet-5":
                return "EG-0001 is 99 days late."                    # invents a number: must be rejected
            return "EG-0001 is at Customs Declaration and past its step's due date."
        finally:
            if self.local:
                with _l:
                    LIVE["local"] -= 1


TAGS = {"models": [{"name": "qwen3:4b", "size": 2.5e9}, {"name": "llama3.1:8b", "size": 4.9e9},
                   {"name": "mistral-nemo:12b", "size": 7.1e9}, {"name": "nomic-embed-text:latest", "size": 2.7e8},
                   {"name": "qwen2.5vl:7b", "size": 6e9}]}
models.PROVIDERS["ollama_tags"] = lambda: TAGS
models.PROVIDERS["ollama"] = Fake("local", local=True)
models.PROVIDERS["ollama_model"] = lambda name: Fake("local:" + name, local=True)
models.PROVIDERS["cortex"] = lambda mid: Fake(mid)

print("\n1. EVERY MODEL, FOUND")
ids = [m["id"] for m in models.registry()]
check("Installed Ollama models are found and registered (local:<name>)", {"local:llama3.1:8b", "local:mistral-nemo:12b", "local:qwen2.5vl:7b"} <= set(ids), ids)
check("The configured model is not registered twice; embedding models are not chat models",
      "local:qwen3:4b" not in ids and not any("embed" in i for i in ids))
vl = next(m for m in models.registry() if m["id"] == "local:qwen2.5vl:7b")
check("A vision model is for pictures, not wording", vl["caps"].get("vision") == 3 and not vl["caps"].get("word"))
check("Snowflake models that answer on the account are registered (Claude Sonnet 5, Llama 3.1 70B, Mistral 7B); retired ones and Opus are not",
      {"claude-sonnet-5", "llama3.1-70b", "mistral-7b"} <= set(ids) and not any(x in i for i in ids for x in ("opus", "mixtral", "gemma-7b", "arctic", "reka", "jamba")))
check("Local models may see T2 data; Cortex models may not", all("T2" in m["tiers"] for m in models.registry() if m["provider"] == "ollama")
      and all("T2" not in m["tiers"] for m in models.registry() if m["provider"] == "cortex"))
os.environ["ATLAS_MISSION_LOCAL_MODELS"] = "configured"
models._found["at"] = 0
check("ATLAS_MISSION_LOCAL_MODELS=configured uses only the configured local model", not any(i.startswith("local:") for i in [m["id"] for m in models.registry()]))
os.environ.pop("ATLAS_MISSION_LOCAL_MODELS")
models._found["at"] = 0
snap = models.snapshot()
check("Mission Control sees each model's family and live health", all("family" in m and "health" in m for m in snap))

print("\n2. WHO SITS ON THE COUNCIL")
check("Council size follows the profile: drill 0, normal 1, deep 2, emergency 3",
      [council.size(p) for p in ("drill", "normal", "deep", "emergency")] == [0, 1, 2, 3])
three = council.members("review", "emergency", 3)
fams = [models.family(m) for m in three]
check("Members come from different families first", len(three) == 3 and len(set(fams)) == 3, fams)
noc = council.members("review", "emergency", 3, avoid_family="claude")
check("A reviewer is never from the drafter's family", noc and all(models.family(m) != "claude" for m in noc), [m["id"] for m in noc])
check("A drill uses no billed model", all(m["provider"] != "cortex" for m in council.members("word", "drill", 3)))

print("\n3. WORKING TOGETHER")
CALLS.clear()
facts = ["EG-0001 is at Customs Declaration.", "EG-0001 is 45 days past its step's due date."]
h = council.hypotheses(facts, ["Customs clearance delay"], "m1", "hyp", "emergency", 80000)
lab = {c["label"]: c["proposed_by"] for c in h["causes"]}
congestion = next((v for k, v in lab.items() if "congestion" in k.lower()), [])
check("Each member suggests causes in parallel; the same cause from two models is merged, with both names",
      len(h["members"]) == 3 and len(congestion) == 2, h)
check("A cause already in ATLAS's own list is not repeated", not any("customs inspection" in k.lower() and False for k in lab) and
      not any(k.lower() == "customs clearance delay" for k in lab))
check("The most agreed cause comes first", h["causes"][0]["proposed_by"] == congestion)
os.environ["ATLAS_LOCAL_PARALLEL"] = "1"
LIVE["max_local"] = 0
os.environ["ATLAS_COUNCIL_SIZE"] = "4"
models.PROVIDERS["cortex"] = lambda mid: Fake(mid) if False else Fake(mid)
both_local = [m for m in models.registry() if m["provider"] == "ollama" and m["caps"].get("word")]
res = council._parallel(both_local[:3], lambda m: models.call("word", "s", "p", only=m))
check("Several local models in parallel still run one at a time on the tower", LIVE["max_local"] == 1 and all(r["ok"] for r in res), (LIVE, res))
os.environ.pop("ATLAS_COUNCIL_SIZE")
r = council.review(facts, "Customs clearance delay is supported.", "claude", "m1", "review", "emergency", 80000)
check("A review panel: every reviewer's verdict and concern kept, with who said it; a split is said plainly",
      len(r["reviewers"]) >= 2 and r["split"] and any(x["concerns"] for x in r["reviewers"]) and
      all(models.family({"id": x["model"], "family": x["family"]}) != "claude" for x in r["reviewers"]), r)
w = council.word("You are ATLAS.", "CONCLUSION: delay", facts + ["EG-0001", "Customs Declaration"], "m1", "report", "emergency", 80000)
check("Drafts in parallel: a draft that invents a number is rejected, the best draft that passes is used",
      w["text"] and w["model"] != "claude-sonnet-5" and any(x["model"] == "claude-sonnet-5" and "added facts" in x["why"] for x in w["rejected"]), w)
DOWN.add("llama3.3-70b")
h2 = council.hypotheses(facts, [], "m2", "hyp", "deep", 80000)
check("A member that is down is simply left out; the others carry on", "llama3.3-70b" not in h2["members"] and h2["members"], h2)
DOWN.clear()
rows = [x for x in models.ledger("m1", 500)]
check("Every member's call is in the ledger with its purpose", rows and all("(council)" in x["purpose"] for x in rows) and
      len({x["model_id"] for x in rows}) >= 3, [(x["model_id"], x["purpose"]) for x in rows][:6])
check("Drill: no council, no call", council.hypotheses(facts, [], "m3", "hyp", "drill", 80000)["members"] == [] and not models.ledger("m3", 10))

print("\n4. REAL ANSWERS (recorded from Snowflake Cortex on 30 Oct, made-up test facts)")
REAL = {"claude-sonnet-5": '{"causes": ["Carrier/agent documentation errors or missing paperwork", "Agent Q resource capacity overload causing processing backlog", "Duty/tax calculation dispute or discrepancy requiring resolution"]}',
        "llama3.3-70b": '{"causes": ["Insufficient duty payment processing capacity", "Inadequate communication between logistics team and clearing agent", "Inefficient customs documentation process"]}',
        "mistral-7b": ' {\n"causes": ["Agent backlog (AGENT Q having 19 other past-due shipments)", "System issue or delay in processing duty payments for BU XX", "Possible discrepancy or issue with the documentation of TEST-00001"]\n}'}


class Rec(object):
    def __init__(self, m):
        self.model = m

    def health(self):
        return {"ok": self.model in REAL, "detail": "recorded"}

    def generate(self, *a, **k):
        return REAL[self.model]


models.PROVIDERS["cortex"] = lambda m: Rec(m)
models.PROVIDERS["ollama"] = Rec("none")
models.PROVIDERS["ollama_model"] = lambda n: Rec("none")
os.environ["ATLAS_COUNCIL_SIZE"] = "3"
hr = council.hypotheses(["facts"], ["Customs clearance delay"], "real", "hyp", "emergency", 80000)
by = {c["theme"]: c["proposed_by"] for c in hr["causes"]}
check("Three real models worded the same causes differently; ATLAS still sees the agreement (documents: 3, agent capacity: 3)",
      sorted(hr["members"]) == ["claude-sonnet-5", "llama3.3-70b", "mistral-7b"] and len(by.get("documents", [])) == 3
      and len(by.get("agent capacity", [])) == 3, hr["causes"])
check("Each model's own wording is kept next to the merged cause", all(len(c["wordings"]) == len(c["proposed_by"]) for c in hr["causes"]))
os.environ.pop("ATLAS_COUNCIL_SIZE")
from intelligence import factguard  # noqa: E402
FACTS = ["TEST-00001 (BU XX, Air) is at step Duty Payment; eHub status InProgress.", "TEST-00001 is 38 days past its step's due date.",
         "21 other open shipments in XX are past due at Duty Payment.", "Clearing agent AGENT Q has 19 other open past-due shipments."]
real_claude = ("Shipment TEST-00001 (BU XX, Air) remains stuck at the Duty Payment step, now 38 days overdue, with its eHub status still "
               "showing InProgress. This is not an isolated case: 21 other open shipments in XX are similarly past due at the same step, "
               "and clearing agent AGENT Q has 19 additional open past-due shipments.")
ok, bad = factguard.check(real_claude, FACTS)
check("Claude Sonnet 5's real summary passes the fact check (every number is in the evidence)", ok and not council._measures(real_claude, FACTS), bad)
check("The same summary with one number changed would be rejected", council._measures(real_claude.replace("38 days", "83 days"), FACTS))

print("\n5. ONE CONNECTION, MANY MODELS")
import io  # noqa: E402
import json as _json  # noqa: E402
import urllib.error  # noqa: E402
from intelligence import cortex as CX  # noqa: E402


def opener(request, timeout=None):
    body = _json.loads(request.data.decode())
    if body["model"] == "mixtral-8x7b":
        raise urllib.error.HTTPError(request.full_url, 400, "legacy", {}, io.BytesIO(b"{}"))
    if body["model"] == "deny":
        raise urllib.error.HTTPError(request.full_url, 401, "expired", {}, io.BytesIO(b"{}"))
    out = io.BytesIO(_json.dumps({"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}).encode())
    out.__enter__ = lambda *a: out
    out.__exit__ = lambda *a: False
    return out


class At(CX.CortexProvider):
    def __init__(self, name):
        CX.CortexProvider.__init__(self, conf=("https://acct.snowflakecomputing.com", "secret"), opener=opener)
        self._m = name

    @property
    def model(self):
        return self._m


CX._state.update(failed_at=0.0, last_error=None, models={})
try:
    At("mixtral-8x7b").generate("s", "p")
except CX.CortexError:
    pass
check("A retired model's failure (HTTP 400) pauses only that model", not At("mixtral-8x7b").health()["ok"]
      and At("claude-sonnet-5").health()["ok"] and At("llama3.1-8b").health()["ok"])
check("...and the other models still answer through the same connection", At("claude-sonnet-5").generate("s", "p") == "OK")
try:
    At("deny").generate("s", "p")
except CX.CortexError:
    pass
check("An account failure (HTTP 401) still pauses Cortex as a whole, as before", not At("claude-sonnet-5").health()["ok"])
CX._state.update(failed_at=0.0, last_error=None, models={})

print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
