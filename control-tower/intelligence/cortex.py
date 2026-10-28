"""
ATLAS's wording model in Mantrac's own Snowflake account (Snowflake Cortex),
in front of the local Ollama model.

Approved by the project owner on 27 Oct 2026: Cortex is billed in Snowflake
credits, unlike the local model, so there is a daily token budget and every
call is counted. Nothing else changes: ATLAS's rules and records decide every
fact, the model only words the answer, and intelligence/factguard rejects any
phrasing that adds a number, reference, link or success claim.

Where it goes and what it sends
  * https://<org>-<account>.snowflakecomputing.com/api/v2/cortex/v1/chat/completions
    (Mantrac's account; only *.snowflakecomputing.com over https is accepted).
  * The answer ATLAS already built and the run evidence it was built from.
    With the account's cross-region setting (AWS_US) Snowflake may process a
    prompt in that region; nothing is stored there.
  * Never: passwords, tokens, cookies, verification codes (the caller masks
    them), and never pictures (a picture is read by the local vision model).

Set up, as the Windows account that runs the tower (not as administrator):
    python -m intelligence.cortex set      account URL + token (token hidden), stored in
                                           Windows Credential Manager, never in a file
    python -m intelligence.cortex status   configured? today's calls and tokens (no secret)
    python -m intelligence.cortex test     one short call, to prove it works
    python -m intelligence.cortex remove   back to the local model only

    ATLAS_CORTEX_MODEL          default claude-sonnet-4-5
    ATLAS_CORTEX_TIMEOUT_S      default 30
    ATLAS_CORTEX_DAILY_TOKENS   default 500000 (then the local model, until midnight)
    ATLAS_CORTEX_CACHE_S        default 900: the same request within 15 minutes is
                                answered from memory (same text, no tokens)
    ATLAS_CORTEX=off            switch Cortex off without removing the token

Tokens are kept low without changing answers: answers ATLAS builds exactly
(records, skills, Clearance, emails, guides) never reach a model; repeated
requests come from memory; prompts carry no empty fields or JSON spacing;
the answer length is capped per task. `status` shows tokens per purpose.
"""

import getpass
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

TARGET = "MantracTower/SnowflakeCortex"
PATH = "/api/v2/cortex/v1/chat/completions"
DEFAULT_MODEL = "claude-sonnet-4-5"
COOL_DOWN_S = 300

_lock = threading.Lock()
_state = {"failed_at": 0.0, "last_error": None}
_cache = {}                        # sha256(request) -> (time, text); paid answers only
CACHE_MAX = 300


def _cache_key(model_name, system, prompt, json_mode, max_tokens):
    raw = json.dumps([model_name, system, prompt, bool(json_mode), int(max_tokens)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_ttl():
    try:
        return max(0, int(os.environ.get("ATLAS_CORTEX_CACHE_S") or 900))
    except ValueError:
        return 900


class CortexError(Exception):
    pass


class _Refuse(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the token to another host: refused."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_NO_REDIRECTS = urllib.request.build_opener(_Refuse)


def _store():
    import hub_credentials                         # Credential Manager (advapi32)
    return hub_credentials.backend()


def allowed_url(url):
    """Only Mantrac-style Snowflake account URLs, over https."""
    u = urlparse(str(url or ""))
    host = (u.hostname or "").lower()
    try:
        port = u.port
    except ValueError:
        return False
    return u.scheme == "https" and host.endswith(".snowflakecomputing.com") and not u.path.strip("/") \
        and "@" not in (u.netloc or "") and not u.query and not u.fragment and port is None


def settings():
    """-> (account_url, token) from Credential Manager, or None. Never printed."""
    if (os.environ.get("ATLAS_CORTEX") or "").strip().lower() in ("0", "off", "no", "false"):
        return None
    store = _store()
    if store is None:
        return None
    try:
        found = store.read(TARGET)
    except Exception:                     # an unreadable entry: the local model answers
        return None
    if not found or not allowed_url(found[0]) or not found[1]:
        return None
    return found[0].rstrip("/"), found[1]


def model():
    return (os.environ.get("ATLAS_CORTEX_MODEL") or DEFAULT_MODEL).strip()


# ── the daily budget ──────────────────────────────────────────────────────

def _usage_path():
    base = Path(os.environ.get("ATLAS_INTEL_DIR") or
                Path(__file__).resolve().parent.parent / "ml" / "data" / "intelligence")
    base.mkdir(parents=True, exist_ok=True)
    return base / "cortex_usage.json"


ZERO = {"calls": 0, "failed": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0}

# What a call was for, from its system prompt (for `status`: where tokens go).
PURPOSES = (("You route questions", "understand"), ("You are ATLAS, the friendly assistant of Mantrac's logistics", "chat"),
            ("You are ATLAS, the friendly assistant of Mantrac's Enterprise", "compose"),
            ("You are ATLAS, the operations assistant", "phrase"))


def purpose(system):
    return next((p for start, p in PURPOSES if str(system or "").startswith(start)), "other")


def _num(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def usage():
    """Today's counts; a missing or damaged file counts as zero, never an error."""
    try:
        with _lock:
            data = json.loads(_usage_path().read_text(encoding="utf-8"))
        day = data.get(time.strftime("%Y-%m-%d")) if isinstance(data, dict) else None
    except Exception:
        day = None
    day = day if isinstance(day, dict) else {}
    out = {k: _num(day.get(k)) for k in ZERO}
    out["by_purpose"] = {k: _num(v) for k, v in (day.get("by_purpose") or {}).items()} \
        if isinstance(day.get("by_purpose"), dict) else {}
    return out


def _count(ok, prompt_tokens=0, completion_tokens=0, what=None, cached=False):
    with _lock:
        path = _usage_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        today = time.strftime("%Y-%m-%d")
        old = data.get(today) if isinstance(data.get(today), dict) else {}
        day = data[today] = {k: _num(old.get(k)) for k in ZERO}
        day["by_purpose"] = {k: _num(v) for k, v in (old.get("by_purpose") or {}).items()} \
            if isinstance(old.get("by_purpose"), dict) else {}
        if cached:
            day["cached"] += 1
        else:
            day["calls" if ok else "failed"] += 1
            day["prompt_tokens"] += _num(prompt_tokens)
            day["completion_tokens"] += _num(completion_tokens)
            if what:
                day["by_purpose"][what] = day["by_purpose"].get(what, 0) + \
                    _num(prompt_tokens) + _num(completion_tokens)
        for old in sorted(data)[:-60]:                 # two months of history
            data.pop(old, None)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(path))


def budget():
    try:
        return int(os.environ.get("ATLAS_CORTEX_DAILY_TOKENS") or 500000)
    except ValueError:
        return 500000


def over_budget():
    u = usage()
    return u["prompt_tokens"] + u["completion_tokens"] >= budget()


# ── the provider (intelligence/llm.py puts it in front of the local one) ───

class CortexProvider(object):
    name = "cortex"

    def __init__(self, conf=None, opener=None):
        self.conf = conf                     # (url, token); tests pass their own
        self.opener = opener or _NO_REDIRECTS.open

    @property
    def model(self):
        return model()

    def _conf(self):
        return self.conf or settings()

    def health(self):
        try:
            return self._health()
        except Exception as error:                  # never breaks ATLAS: the local model answers
            return {"ok": False, "provider": "cortex", "model": self.model,
                    "detail": "Cortex settings could not be read ({0})".format(type(error).__name__)}

    def _health(self):
        conf = self._conf()
        if not conf:
            return {"ok": False, "provider": "cortex", "model": None,
                    "detail": "Snowflake Cortex is not set up (python -m intelligence.cortex set)"}
        if over_budget():
            return {"ok": False, "provider": "cortex", "model": self.model,
                    "detail": "today's Cortex token budget is used ({0:,}); the local model answers "
                              "until midnight".format(budget())}
        if time.time() - _state["failed_at"] < COOL_DOWN_S:
            return {"ok": False, "provider": "cortex", "model": self.model,
                    "detail": "Cortex failed a moment ago ({0}); trying again shortly".format(_state["last_error"])}
        return {"ok": True, "provider": "cortex", "model": self.model, "detail": "ready"}

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=700, images=None):
        if images:
            raise CortexError("pictures are read by the local vision model")
        if not self.health()["ok"]:
            raise CortexError(self.health()["detail"])
        url, token = self._conf()
        if not allowed_url(url):
            raise CortexError("the Cortex URL is not a Snowflake account URL")
        what = purpose(system)
        key = _cache_key(self.model, system, prompt, json_mode, max_tokens)
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _cache_ttl():
            try:
                _count(True, what=what, cached=True)
            except Exception:
                pass
            return hit[1]
        if json_mode:
            system = system + "\nReply with one JSON object only, no other text."
        body = {"model": self.model, "stream": False, "temperature": 0.2,
                "max_completion_tokens": int(max_tokens),
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}]}
        request = urllib.request.Request(
            url + PATH, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + token,
                     "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN"})
        own = float(os.environ.get("ATLAS_CORTEX_TIMEOUT_S") or 30)
        limit = min(float(timeout), own) if timeout else own     # the caller's wait, at most Cortex's own
        started = time.perf_counter()
        try:
            with self.opener(request, timeout=limit) as response:
                answer = json.loads(response.read().decode("utf-8") or "{}")
            try:
                from . import trace
                usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
                trace.model({"model": "cortex:" + str(self.model), "wall_ms": (time.perf_counter() - started) * 1000,
                             "prompt_tokens": usage.get("prompt_tokens"), "out_tokens": usage.get("completion_tokens")})
            except Exception:
                pass
        except urllib.error.HTTPError as error:
            self._fail("Snowflake answered HTTP {0}{1}".format(error.code, {
                401: " (token expired or wrong)", 403: " (the user's role lacks Cortex, or a network policy refused this server)",
                404: " (model or endpoint not available in this account)", 429: " (rate limited)"}.get(error.code, "")))
        except (urllib.error.URLError, OSError, ValueError) as error:
            self._fail("Snowflake is not reachable ({0})".format(str(getattr(error, "reason", error))[:100]))
        try:
            text = (answer["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            text = ""
        if not text:
            self._fail("Cortex returned no text")
        used = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
        try:
            # No usage block: count what was allowed, so the budget still holds.
            _count(True, used.get("prompt_tokens") or len(system + prompt) // 3,
                   used.get("completion_tokens") or max_tokens, what=what)
        except Exception:
            pass                      # the answer was paid for: it is used even if not counted
        if _cache_ttl():
            with _lock:
                if len(_cache) >= CACHE_MAX:
                    for old in sorted(_cache, key=lambda k: _cache[k][0])[:CACHE_MAX // 4]:
                        _cache.pop(old, None)
                _cache[key] = (time.time(), text)
        return text

    def _fail(self, why):
        _state.update(failed_at=time.time(), last_error=why)
        try:
            _count(False)
        except OSError:
            pass
        raise CortexError(why)


# ── command line ──────────────────────────────────────────────────────────

def _set(out=print, ask=input, secret=getpass.getpass):
    store = _store()
    if store is None:
        out("Windows Credential Manager is needed (run this on the tower server).")
        return 1
    url = ask("Snowflake account URL [https://pecfylf-fy56654.snowflakecomputing.com]: ").strip() or \
        "https://pecfylf-fy56654.snowflakecomputing.com"
    if not allowed_url(url):
        out("That is not a Snowflake account URL (https://<org>-<account>.snowflakecomputing.com).")
        return 1
    token = secret("Programmatic access token for the ATLAS service user (hidden): ").strip()
    if len(token) < 20:
        out("Nothing stored: that does not look like a programmatic access token.")
        return 1
    store.write(TARGET, url.rstrip("/"), token)
    out("Stored in Windows Credential Manager ({0}). Restart the tower to use it.".format(TARGET))
    return 0


def _status(out=print):
    conf = settings()
    u = usage()
    out("Snowflake Cortex: {0}".format("set up, model " + model() + ", account " + conf[0] if conf else
                                      "not set up (python -m intelligence.cortex set)"))
    out("Today: {0} calls, {1} answered from memory (no tokens), {2} failed, {3:,} tokens of {4:,}".format(
        u["calls"], u["cached"], u["failed"], u["prompt_tokens"] + u["completion_tokens"], budget()))
    if u["by_purpose"]:
        out("Tokens by purpose: " + ", ".join("{0} {1:,}".format(k, v) for k, v in
                                              sorted(u["by_purpose"].items(), key=lambda x: -x[1])))
    return 0 if conf else 1


def _test(out=print):
    p = CortexProvider()
    try:
        text = p.generate("You answer in one short sentence.", "Say hello to the Mantrac logistics team.",
                          max_tokens=40)
        out("Cortex answered ({0}): {1}".format(p.model, text[:200]))
        return 0
    except CortexError as error:
        out("Cortex did not answer: {0}".format(error))
        return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    if cmd == "set":
        return _set()
    if cmd == "status":
        return _status()
    if cmd == "test":
        return _test()
    if cmd == "remove":
        store = _store()
        print("Removed." if store and store.delete(TARGET) else "There was nothing to remove.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())
