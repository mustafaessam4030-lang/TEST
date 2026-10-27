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
check("no Snowflake SDK or other package needed (standard library only)", "import snowflake" not in src and "requests" not in src)
sql = open(os.path.join(HERE, "deploy", "snowflake", "atlas_cortex_setup.sql"), encoding="utf-8").read()
check("the setup script: a service user with Cortex only, a network policy, a role-restricted token",
      "TYPE = SERVICE" in sql and "CORTEX_REST_API_USER" in sql and "NETWORK POLICY" in sql
      and "ROLE_RESTRICTION = 'ATLAS_CORTEX_ROLE'" in sql and "GRANT SELECT" not in sql.upper())

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
