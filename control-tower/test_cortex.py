"""
ATLAS's wording model in Snowflake Cortex (intelligence/cortex.py) in front of
the local model: the request Snowflake gets, the token never printed, only a
Snowflake account URL accepted, the daily budget, and the local model taking
over whenever Cortex cannot answer. Snowflake is a stand-in here: no call is
made and nothing is billed.

    python test_cortex.py
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="cortex_")
os.environ.pop("ATLAS_CORTEX", None)
os.environ["ATLAS_CORTEX_CACHE_S"] = "0"          # each check makes its own call; the memory has its own section

from intelligence import cortex as C, llm                     # noqa: E402
import hub_credentials as H                                    # noqa: E402

PASS, FAIL = [], []
TOKEN = "pat-" + "x" * 60


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(str(detail)[:300])))


class Store(object):
    def __init__(self):
        self.d = {}

    def read(self, t):
        return self.d.get(t)

    def write(self, t, u, s):
        self.d[t] = (u, s)

    def delete(self, t):
        return self.d.pop(t, None) is not None


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SEEN = []


def snowflake(answer=None, status=None, tokens=(120, 40)):
    def opener(request, timeout=None):
        SEEN.append({"url": request.full_url, "headers": dict(request.header_items()),
                     "body": json.loads(request.data.decode()), "timeout": timeout})
        if status:
            raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(b"{}"))
        return Resp(json.dumps({"choices": [{"message": {"content": answer or "Hello from Cortex."}}],
                                "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}}).encode())
    return opener


print("1. ONLY MANTRAC'S SNOWFLAKE")
check("a Snowflake account URL over https is accepted", C.allowed_url("https://pecfylf-fy56654.snowflakecomputing.com"))
for bad in ("http://pecfylf-fy56654.snowflakecomputing.com", "https://evil.example.com",
            "https://x.snowflakecomputing.com.evil.example", "https://u:p@x.snowflakecomputing.com",
            "https://x.snowflakecomputing.com/redirect"):
    check("refused: " + bad, not C.allowed_url(bad))

print("\n2. THE REQUEST")
SEEN.clear()
p = C.CortexProvider(("https://pecfylf-fy56654.snowflakecomputing.com", TOKEN), snowflake("Done."))
text = p.generate("SYSTEM", "PROMPT", max_tokens=300)
r = SEEN[-1]
check("it answers with the model's text", text == "Done.")
check("the chat completions endpoint of the account", r["url"] == "https://pecfylf-fy56654.snowflakecomputing.com/api/v2/cortex/v1/chat/completions")
check("the token as a programmatic access token", r["headers"].get("Authorization") == "Bearer " + TOKEN
      and r["headers"].get("X-snowflake-authorization-token-type") == "PROGRAMMATIC_ACCESS_TOKEN", r["headers"])
check("model, system + user, no streaming, max_completion_tokens (max_tokens is refused by Snowflake)",
      r["body"]["model"] == "claude-sonnet-4-5" and r["body"]["stream"] is False and r["body"]["max_completion_tokens"] == 300
      and "max_tokens" not in r["body"] and [m["role"] for m in r["body"]["messages"]] == ["system", "user"])
check("the tokens used are counted for today", C.usage()["calls"] == 1 and C.usage()["prompt_tokens"] == 120)
try:
    p.generate("S", "P", images=["aGk="])
    check("a picture is never sent to Cortex", False)
except C.CortexError:
    check("a picture is never sent to Cortex", len(SEEN) == 1)

print("\n3. WHEN CORTEX CANNOT ANSWER, THE LOCAL MODEL DOES")


class Local(object):
    name, model = "ollama", "qwen3.5:4b"

    def health(self):
        return {"ok": True, "provider": "ollama", "model": self.model, "detail": "ready"}

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=700, images=None):
        return "local answer" + (" with picture" if images else "")


C._state.update(failed_at=0.0, last_error=None)
chain = llm.ChainProvider(C.CortexProvider(("https://pecfylf-fy56654.snowflakecomputing.com", TOKEN), snowflake(status=401)), Local())
check("a refused token (401): the local model answers", chain.generate("S", "P") == "local answer")
check("…and Cortex rests for a few minutes, saying why", "token expired or wrong" in C.CortexProvider(("https://pecfylf-fy56654.snowflakecomputing.com", TOKEN)).health()["detail"])
C._state.update(failed_at=0.0, last_error=None)
chain = llm.ChainProvider(C.CortexProvider(("https://pecfylf-fy56654.snowflakecomputing.com", TOKEN), snowflake("cortex answer")), Local())
check("Cortex answers first when it can", chain.generate("S", "P") == "cortex answer" and chain.model == "claude-sonnet-4-5")
check("a picture goes to the local vision model", chain.generate("S", "P", images=["x"]) == "local answer with picture")
os.environ["ATLAS_CORTEX_DAILY_TOKENS"] = "100"
check("over today's budget: the local model, and the reason is shown",
      chain.generate("S", "P") == "local answer" and "budget" in C.CortexProvider(("https://a.snowflakecomputing.com", TOKEN)).health()["detail"])
os.environ.pop("ATLAS_CORTEX_DAILY_TOKENS")

print("\n3b. NOTHING ABOUT CORTEX CAN BREAK AN ANSWER")
import time as _t                                              # noqa: E402
open(C._usage_path(), "w").write(json.dumps({_t.strftime("%Y-%m-%d"): {"calls": 1}}))
C._state.update(failed_at=0.0, last_error=None)
chain = llm.ChainProvider(C.CortexProvider(("https://pecfylf-fy56654.snowflakecomputing.com", TOKEN), snowflake("ok")), Local())
check("a usage file of the wrong shape counts as zero, not an error", C.usage()["prompt_tokens"] == 0 and chain.generate("S", "P") == "ok")
open(C._usage_path(), "w").write("[1, 2, 3]")
check("…and a usage file that is not even an object", chain.health()["ok"] and chain.generate("S", "P") == "ok")


class Broken(C.CortexProvider):
    def _conf(self):
        raise UnicodeDecodeError("utf-16-le", b"x", 0, 1, "odd length")


chain = llm.ChainProvider(Broken(), Local())
check("an unreadable stored token: health says why, the local model answers",
      chain.health()["ok"] and chain.generate("S", "P") == "local answer" and chain.model == "qwen3.5:4b")
check("the answer is labelled with the model that actually answered", chain.answered_by == "qwen3.5:4b")
for bad in ("https://x.snowflakecomputing.com?a=1", "https://x.snowflakecomputing.com:8443", "https://x.snowflakecomputing.com#f"):
    check("refused: " + bad, not C.allowed_url(bad))
try:
    C._Refuse().redirect_request(type("R", (), {"full_url": "https://a.snowflakecomputing.com/x"})(), None, 302, "Found", {}, "https://evil.example/")
    check("a redirect is refused (the token never follows it)", False)
except urllib.error.HTTPError:
    check("a redirect is refused (the token never follows it)", True)
SEEN.clear()
C._state.update(failed_at=0.0, last_error=None)
C.CortexProvider(("https://a.snowflakecomputing.com", TOKEN), snowflake("x")).generate("S", "P", timeout=5)
check("the caller's shorter wait is respected", SEEN[-1]["timeout"] == 5.0)
before = C.usage()
def no_usage(request, timeout=None):
    return Resp(json.dumps({"choices": [{"message": {"content": "y"}}]}).encode())
C.CortexProvider(("https://a.snowflakecomputing.com", TOKEN), no_usage).generate("S", "P", max_tokens=200)
check("no usage block from Snowflake: the allowed tokens are still counted", C.usage()["completion_tokens"] - before["completion_tokens"] == 200)

print("\n3c. FEWER TOKENS, SAME ANSWERS")
os.environ["ATLAS_CORTEX_CACHE_S"] = "900"
C._cache.clear(); SEEN.clear(); C._state.update(failed_at=0.0, last_error=None)
pc = C.CortexProvider(("https://a.snowflakecomputing.com", TOKEN), snowflake("same words"))
u0 = C.usage()
first = pc.generate("You route questions for ATLAS", "Question: where is X?", json_mode=True, max_tokens=160)
second = pc.generate("You route questions for ATLAS", "Question: where is X?", json_mode=True, max_tokens=160)
u1 = C.usage()
check("the same request within 15 minutes comes from memory: same text, one call", first == second == "same words" and len(SEEN) == 1)
check("…counted as answered from memory, no tokens", u1["cached"] - u0["cached"] == 1 and u1["calls"] - u0["calls"] == 1
      and u1["prompt_tokens"] - u0["prompt_tokens"] == 120)
pc.generate("You route questions for ATLAS", "Question: where is Y?", json_mode=True, max_tokens=160)
check("a different request is a new call", len(SEEN) == 2)
check("tokens are counted per purpose", C.usage()["by_purpose"].get("understand", 0) >= 320, C.usage()["by_purpose"])
os.environ["ATLAS_CORTEX_CACHE_S"] = "0"
packed = llm.compact({"ref": "MEDU1", "eta": None, "notes": [], "events": [{"a": 1, "b": ""}], "x": "y"})
check("prompts carry no empty fields or JSON spacing (same facts)", packed == '{"ref":"MEDU1","events":[{"a":1}],"x":"y"}', packed)
from intelligence import converse as CV                          # noqa: E402
check("answers ATLAS builds exactly never reach a model (Clearance, emails, guides, screenshots)",
      {"skill_clearance", "setup_admin_only", "evidence", "code_request"} <= CV.SKILL_INTENTS)

os.environ["ATLAS_LLM_SLOTS"] = "1"
_real = llm.provider
_chain = llm.ChainProvider(C.CortexProvider(("https://a.snowflakecomputing.com", TOKEN), snowflake("x")), Local())
llm.provider = lambda: _chain
check("while Cortex answers, 4 questions run at once (the local model's gate stays 1)", CV._slots() == 4)
_local = Local()
llm.provider = lambda: _local
check("…and back to 1 with the local model alone", CV._slots() == 1)
llm.provider = _real
os.environ.pop("ATLAS_LLM_SLOTS")

print("\n4. SET UP ON THE SERVER (Credential Manager)")
H._backend = Store()
out = []
code = C._set(out=out.append, ask=lambda q: "", secret=lambda q: TOKEN)
check("set stores the default account URL and the hidden token", code == 0 and H._backend.read(C.TARGET) ==
      ("https://pecfylf-fy56654.snowflakecomputing.com", TOKEN))
check("nothing printed shows the token", TOKEN not in " ".join(out))
code = C._set(out=out.append, ask=lambda q: "https://evil.example.com", secret=lambda q: TOKEN)
check("a URL that is not Snowflake is refused", code == 1)
llm._provider.update(key=None, obj=None)
os.environ["ATLAS_LLM_PROVIDER"] = "ollama"
check("with the token stored, ATLAS's provider is Cortex first, local second",
      isinstance(llm.provider(), llm.ChainProvider) and isinstance(llm.provider().first, C.CortexProvider))
os.environ["ATLAS_CORTEX"] = "off"
check("ATLAS_CORTEX=off: the local model only, token kept", not isinstance(llm.provider(), llm.ChainProvider)
      and H._backend.read(C.TARGET) is not None)
os.environ.pop("ATLAS_CORTEX")
st = []
C._status(out=st.append)
check("status names the model and today's tokens, never the token", "claude-sonnet-4-5" in st[0] and TOKEN not in " ".join(st))
H._backend = None
llm._provider.update(key=None, obj=None)

src = open(os.path.join(HERE, "intelligence", "cortex.py"), encoding="utf-8").read()
check("no Snowflake SDK or other package needed (standard library only)", "import snowflake" not in src and "import requests" not in src)
sql = open(os.path.join(HERE, "deploy", "snowflake", "atlas_cortex_setup.sql"), encoding="utf-8").read()
check("the setup script: a service user with Cortex only, a network policy, a role-restricted token",
      "TYPE = SERVICE" in sql and "CORTEX_REST_API_USER" in sql and "NETWORK POLICY" in sql
      and "ROLE_RESTRICTION = 'ATLAS_CORTEX_ROLE'" in sql and "GRANT SELECT" not in sql.upper()
      and sql.index("CREATE DATABASE IF NOT EXISTS GOVERNANCE") < sql.index("CREATE SCHEMA IF NOT EXISTS GOVERNANCE.NETWORK"))

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
