"""
Semantic candidates for the Knowledge Engine — optional, local, off by default.

    ATLAS_EMBED_MODEL   an embedding model served by the local Ollama, e.g.
                        qwen3-embedding:0.6b or bge-m3. Unset = off (keyword
                        retrieval only, exactly as before).
    ATLAS_EMBED_URL     default ATLAS_LLM_URL, else http://127.0.0.1:11434
    ATLAS_EMBED_TIMEOUT_S   per question, default 2 s: past it, keyword
                        retrieval answers alone (a long model answer elsewhere
                        never makes a definition wait)

What it does and does not do:
  - It proposes WHICH item a knowledge question is about, when the question's
    words are far from the item's. It never decides WHETHER a question is a
    knowledge question: kb_answer's routing does that first, so a high
    similarity cannot turn "weather in Cairo" or "where is my container" into
    a definition.
  - Item vectors are stored in the ATLAS store (kb_embed_<model>.json), keyed by
    item id and a hash of the text embedded: a corrected, superseded or new
    item is re-embedded; nothing else is. Retired and superseded items are
    never candidates (the knowledge base's own current() check).
  - Nothing leaves the machine: the model runs in the local Ollama.
"""
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.request

_lock = threading.Lock()
_state = {"model": None, "sig": None, "ids": [], "vecs": [], "error": None, "at": 0}
_qcache = {}

# Query instructions the models were trained with (documents take none).
QUERY_PREFIX = {
    "qwen3-embedding": "Instruct: Given a logistics question, retrieve the knowledge entry that answers it\nQuery: ",
    "e5": "query: ",
}


def model():
    return (os.environ.get("ATLAS_EMBED_MODEL") or "").strip() or None


def enabled():
    return model() is not None


def _url():
    return (os.environ.get("ATLAS_EMBED_URL") or os.environ.get("ATLAS_LLM_URL") or "http://127.0.0.1:11434").rstrip("/")


def _embed(texts, timeout):
    body = json.dumps({"model": model(), "input": list(texts)}).encode("utf-8")
    req = urllib.request.Request(_url() + "/api/embed", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    # A local model only: never a proxy, never another host's.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8") or "{}")
    vecs = out.get("embeddings") or []
    if len(vecs) != len(texts):
        raise ValueError("the embedding model returned {0} vectors for {1} texts".format(len(vecs), len(texts)))
    return [_norm(v) for v in vecs]


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def doc_text(it):
    """What is embedded for an item: its names and its definitions."""
    names = [it.get("term"), it.get("acronym"), it.get("full_form")] + list(it.get("synonyms") or []) + \
        list(it.get("terms_ar") or [])
    return "; ".join(n for n in names if n) + ". " + (it.get("definition") or "") + " " + (it.get("definition_ar") or "")


def _prefix(name):
    for key, p in QUERY_PREFIX.items():
        if key in name:
            return p
    return ""


def _store_path(name):
    from . import store
    return store.folder() / "kb_embed_{0}.json".format(re.sub(r"[^A-Za-z0-9_.-]", "_", name))


def _index(k):
    """The vectors for the current items, re-embedding only what changed."""
    name = model()
    current = [(iid, it) for iid, it in k.items.items() if k.current(it)]
    sig = (name, tuple(sorted((iid, hashlib.sha1(doc_text(it).encode("utf-8")).hexdigest()[:16]) for iid, it in current)))
    if _state["sig"] == sig:
        return
    try:
        saved = json.loads(_store_path(name).read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    want = dict(sig[1])
    todo = [(iid, it) for iid, it in current if (saved.get(iid) or {}).get("h") != want[iid]]
    for i in range(0, len(todo), 16):
        chunk = todo[i:i + 16]
        vecs = _embed([doc_text(it) for _, it in chunk], timeout=120)
        for (iid, _), v in zip(chunk, vecs):
            saved[iid] = {"h": want[iid], "v": [round(x, 6) for x in v]}
    saved = {iid: saved[iid] for iid in want if iid in saved}
    if todo:
        try:
            from . import store
            if store.admitted():
                _store_path(name).write_text(json.dumps(saved), encoding="utf-8")
        except Exception:
            pass
    _state.update(model=name, sig=sig, ids=list(saved), vecs=[saved[i]["v"] for i in saved], error=None)


def warm(k=None):
    """Build the index ahead of the first question (the server calls it in the
    background at start). Never raises."""
    if not enabled():
        return False
    try:
        from . import kb as KB
        with _lock:
            _index(k or KB.kb())
        return True
    except Exception as e:
        _state["error"] = "{0}: {1}".format(type(e).__name__, str(e)[:120])
        return False


def candidates(question, k, limit=5):
    """[(item_id, cosine)] best first, or [] when off, not ready or too slow."""
    if not enabled():
        return []
    try:
        timeout = float(os.environ.get("ATLAS_EMBED_TIMEOUT_S") or 2)
    except ValueError:
        timeout = 2.0
    try:
        from . import trace
        with trace.span("kb.embed"):
            if not _lock.acquire(timeout=timeout):
                return []                      # the index is being built: keyword retrieval answers
            try:
                _index(k)
            finally:
                _lock.release()
            key = (model(), question)
            qv = _qcache.get(key)
            if qv is None:
                qv = _embed([_prefix(model()) + question], timeout=timeout)[0]
                if len(_qcache) > 512:
                    _qcache.clear()
                _qcache[key] = qv
            scores = [(iid, sum(a * b for a, b in zip(qv, v))) for iid, v in zip(_state["ids"], _state["vecs"])]
    except Exception as e:
        _state["error"] = "{0}: {1}".format(type(e).__name__, str(e)[:120])
        return []
    scores = [(i, s) for i, s in scores if i in k.items and k.current(k.items[i])]
    scores.sort(key=lambda x: -x[1])
    return scores[:limit]


def status():
    return {"model": model(), "items": len(_state["ids"]), "error": _state["error"]}
