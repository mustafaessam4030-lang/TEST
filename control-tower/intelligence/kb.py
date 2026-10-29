"""
The ATLAS Logistics Knowledge Engine: what ATLAS knows about logistics,
written down as versioned records it can search, cite and explain.

    knowledge/curriculum.json   the domains (A fundamentals ... F problem
                                solving, P this platform's own rules)
    knowledge/sources.json      every source an item may cite, with what was
                                actually checked and when
    knowledge/items/*.json      the items, reviewed in git like code
    the ATLAS store             what changed at run time through review
                                (intelligence/kb_govern.py): approvals,
                                corrections, supersessions, retirements

An item is never more authoritative than its review and evidence statuses
say. Search returns them with the item, and the answer says them: an item
that is not yet approved by a Mantrac reviewer is labelled so; a superseded
or retired item is never answered as current; a country rule past its review
date is flagged as possibly out of date.

Retrieval is ranked keyword search (BM25) over each item's term, synonyms,
acronym, Arabic terms and text, with acronym, synonym and Arabic-spelling
expansion. There is no embedding model in this platform, so there is no
semantic search; a question worded far from an item's words can miss it, and
the answer then says nothing was found rather than guessing.
"""
import json
import math
import re
import threading
import time
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "knowledge"

KINDS = ("concept", "term", "kpi", "process", "document", "rule", "case", "party", "incoterm", "lesson")
REVIEW = ("unreviewed", "reviewed", "approved", "superseded", "retired")
EVIDENCE = ("project_verified", "source_cited", "general_knowledge", "unverified")
# general: logistics knowledge; project: this platform's code; business:
# Mantrac's own rules from an approved internal document; jurisdiction: a
# country's rule.
SCOPES = ("general", "project", "business", "jurisdiction")
CURRENT = ("unreviewed", "reviewed", "approved")       # answered as current knowledge
ID = re.compile(r"^[A-FP]\.[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KPI_FIELDS = ("formula", "unit", "aggregation", "period", "fields", "exclusions", "limitations", "convention")
REQUIRED = ("id", "kind", "term", "definition", "domain", "category", "difficulty", "evidence_status", "scope")

_lock = threading.Lock()
_cache = {"sig": None, "kb": None}


# ── text ────────────────────────────────────────────────────────────────────

_AR_DIACRITICS = re.compile("[ً-ْٰـ]")


def norm_ar(text):
    """Arabic spelled one way: no diacritics or tatweel; alef, ya and taa
    marbuta forms folded (أ إ آ -> ا, ى -> ي, ة -> ه)."""
    text = _AR_DIACRITICS.sub("", text)
    return text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}))


STOP = set("""a an the and or of to in on for is are was were be been being it its this that these those
what which who whom how why when where do does did can could should would will shall may might must
i we you he she they me my our your their with from by at as about into than then there here not no yes
if so just please tell show give get explain much many any some all one also more most other such
mean means meaning define definition difference between versus vs""".split())
# Arabic particles that carry no topic.
STOP_AR = set(norm_ar(w) for w in "ما هو هي ماذا ايه إيه يعني في من الى إلى على عن و او أو ال هل كيف ازاي اشرح الفرق بين".split())


# Plurals whose singular means something else: "goods" (cargo) is not
# "good" ("are you good?" was answered with the cargo entry).
NO_STEM = {"goods"}


def _stem(w):
    if w in NO_STEM:
        return w
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text):
    """Words to match on: English lower-cased and singular, Arabic folded and
    stripped of the article; stop words dropped."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    out = []
    # Arabic letters only: the Arabic question mark, comma and semicolon
    # are punctuation, not part of a word.
    for raw in re.findall(r"[A-Za-z0-9]+(?:[/&-][A-Za-z0-9]+)*|[\u0621-\u063A\u0641-\u0652\u0660-\u0669\u0671-\u06D3]+", text):
        if re.match(r"[؀-ۿ]", raw):
            w = norm_ar(raw)
            if w.startswith("ال") and len(w) > 3:
                w = w[2:]
            if w in STOP_AR or len(w) < 2:
                continue
            out.append(w)
            continue
        w = raw.lower()
        if w in STOP:
            continue
        for part in re.split(r"[/&-]", w) if re.search(r"[/&-]", w) else [w]:
            if part and part not in STOP and (len(part) > 1 or part.isdigit()):
                out.append(_stem(part))
        if re.search(r"[/&-]", w):
            out.append(w.replace("/", "").replace("&", "").replace("-", ""))   # b/l -> bl, e-awb -> eawb
    return out


def name_keys(text):
    """The ways a name (or a question) reads for exact-name matching: as
    written, and with hyphens and slashes as spaces."""
    keys = []
    for variant in (str(text or ""), re.sub(r"(?<=[A-Za-z])[-/](?=[A-Za-z])", " ", str(text or ""))):
        key = " ".join(tokens(variant))
        if key and key not in keys:
            keys.append(key)
    return keys


# ── loading and validation ──────────────────────────────────────────────────

def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _files():
    return sorted((KB_DIR / "items").glob("*.json"))


def _signature():
    files = [KB_DIR / "curriculum.json", KB_DIR / "sources.json"] + _files()
    sig = [(str(f), f.stat().st_mtime) for f in files if f.exists()]
    try:
        from . import kb_govern
        sig.append(("govern", kb_govern.signature()))
    except Exception:
        pass
    return tuple(sig)


def validate(item, sources, ids=None):
    """Problems with one item, as sentences. Empty when it is sound."""
    p = []
    for k in REQUIRED:
        if item.get(k) in (None, "", []):
            p.append("missing " + k)
    iid = item.get("id") or "?"
    if item.get("id") and not ID.match(item["id"]):
        p.append("id {0!r} is not 'D.slug' (domain letter, dot, lower-case words)".format(iid))
    if item.get("id") and item.get("domain") and not item["id"].startswith(item["domain"] + "."):
        p.append("id does not start with its domain")
    if item.get("kind") and item["kind"] not in KINDS:
        p.append("unknown kind " + str(item["kind"]))
    if item.get("evidence_status") and item["evidence_status"] not in EVIDENCE:
        p.append("unknown evidence_status " + str(item["evidence_status"]))
    if item.get("review_status", "unreviewed") not in REVIEW:
        p.append("unknown review_status " + str(item.get("review_status")))
    if item.get("scope") and item["scope"] not in SCOPES:
        p.append("unknown scope " + str(item["scope"]))
    if item.get("difficulty") not in (1, 2, 3):
        p.append("difficulty must be 1, 2 or 3")
    cited = item.get("sources") or []
    for s in cited:
        if not isinstance(s, dict) or s.get("source") not in sources:
            p.append("cites an unknown source {0!r}".format(s.get("source") if isinstance(s, dict) else s))
    kinds = {sources[s["source"]].get("authority") for s in cited if isinstance(s, dict) and s.get("source") in sources}
    if item.get("evidence_status") == "project_verified" and "project" not in kinds:
        p.append("project_verified needs a project source (a file in this repository)")
    if item.get("evidence_status") == "source_cited" and not (kinds - {"project"}):
        p.append("source_cited needs an outside source")
    if item.get("scope") == "project" and item.get("evidence_status") != "project_verified":
        p.append("a project rule must be project_verified")
    if item.get("scope") == "business" and "internal" not in kinds:
        p.append("a business rule cites the approved internal document it comes from")
    if item.get("scope") == "jurisdiction":
        if not item.get("jurisdiction"):
            p.append("a jurisdiction item names its country (ISO code)")
        if not item.get("review_by") or not DATE.match(str(item.get("review_by"))):
            p.append("a jurisdiction item needs review_by (YYYY-MM-DD)")
        if "government" not in kinds:
            p.append("a jurisdiction item cites the national authority")
    for k in ("effective_from", "review_by", "created", "updated"):
        if item.get(k) and not DATE.match(str(item[k])):
            p.append("{0} must be YYYY-MM-DD".format(k))
    if item.get("kind") == "kpi":
        kpi = item.get("kpi") or {}
        for k in KPI_FIELDS:
            if kpi.get(k) in (None, "", []):
                p.append("KPI missing " + k)
        if kpi.get("convention") not in (None, "industry", "business", "atlas"):
            p.append("KPI convention must be industry, business or atlas")
    if ids is not None:
        for r in item.get("related") or []:
            if not isinstance(r, dict) or r.get("id") not in ids:
                p.append("related id {0!r} does not exist".format(r.get("id") if isinstance(r, dict) else r))
    if item.get("superseded_by") and ids is not None and item["superseded_by"] not in ids:
        p.append("superseded_by points nowhere")
    return p


class KB(object):
    """The loaded knowledge: items by id, their index, and what failed to load."""

    def __init__(self):
        self.curriculum = _read(KB_DIR / "curriculum.json")
        self.sources = {s["id"]: s for s in _read(KB_DIR / "sources.json")["sources"]}
        self.domains = {d["id"]: d for d in self.curriculum["domains"]}
        self.items, self.errors, self.files = {}, [], {}
        for f in _files():
            try:
                doc = _read(f)
            except (OSError, ValueError) as e:
                self.errors.append("{0}: {1}".format(f.name, e))
                continue
            for it in doc.get("items") or []:
                it = dict(it)
                it.setdefault("review_status", "unreviewed")
                it.setdefault("version", 1)
                it.setdefault("created", doc.get("created") or "2026-10-28")
                it.setdefault("updated", it.get("created"))
                it["origin"] = "repository:knowledge/items/" + f.name
                if it.get("id") in self.items:
                    self.errors.append("{0}: duplicate id {1}".format(f.name, it.get("id")))
                    continue
                self.items[it.get("id")] = it
                self.files[it.get("id")] = f.name
        # What review changed at run time (approvals, corrections, new items,
        # supersession, retirement) — applied over the repository's text.
        try:
            from . import kb_govern
            kb_govern.apply(self)
        except Exception as e:                       # a broken store never hides the KB
            self.errors.append("governance overlay: {0}".format(type(e).__name__))
        ids = set(self.items)
        # An item that fails its schema is never searched or answered (fail
        # closed); it is kept in self.invalid with its problems for the reviewers.
        self.invalid = {}
        for iid, it in list(self.items.items()):
            problems = validate(it, self.sources, ids)
            if problems:
                self.errors.append("{0}: {1}".format(iid, "; ".join(problems)))
                self.invalid[iid] = self.items.pop(iid)
        self._index()

    # ── index ──
    FIELDS = (("term", 4.0), ("synonyms", 3.5), ("acronym", 4.0), ("full_form", 3.0), ("terms_ar", 3.5),
              ("definition", 1.6), ("definition_ar", 1.6), ("explanation", 1.0), ("example", 0.5),
              ("scenario", 0.5), ("category", 0.8))

    def _index(self):
        self.docs, self.df, self.names = {}, {}, {}
        for iid, it in self.items.items():
            tf, n = {}, 0
            for field, weight in self.FIELDS:
                v = it.get(field)
                text = " ".join(v) if isinstance(v, list) else str(v or "")
                for t in tokens(text):
                    tf[t] = tf.get(t, 0.0) + weight
                    n += weight
            self.docs[iid] = (tf, n)
            for t in tf:
                self.df[t] = self.df.get(t, 0) + 1
            # Names a question can say outright: term, acronym, synonyms, Arabic
            # — each also with its hyphens as spaces ("on-time" = "on time").
            for name in [it.get("term"), it.get("acronym"), it.get("full_form")] + list(it.get("synonyms") or []) + \
                    list(it.get("terms_ar") or []):
                for key in name_keys(name):
                    # A number alone, or a fragment left when common words are
                    # dropped ("5" from "5 whys"), is not a name.
                    if re.fullmatch(r"[\d\s]+", key) or (len(key) < 3 and " " not in key and
                                                          str(it.get("acronym") or "").lower() != key):
                        continue
                    self.names.setdefault(key, set()).add(iid)
        self.avg = (sum(n for _tf, n in self.docs.values()) / float(len(self.docs))) if self.docs else 1.0
        # How many items' text uses a name's words: a one-word name many items
        # use ("stock", "sea") is generic; "CFR" or "blank sailing" is not.
        self.name_df = {key: sum(1 for tf, _n in self.docs.values() if all(t in tf for t in key.split()))
                        for key in self.names}
        # The concept graph's reverse edges, so "what points here" is a lookup.
        self.incoming = {}
        for iid, it in self.items.items():
            for r in it.get("related") or []:
                if isinstance(r, dict) and r.get("id") in self.items:
                    self.incoming.setdefault(r["id"], []).append((iid, r.get("type", "related")))

    def _specific(self, name, it, query):
        """A name that says which concept is meant: several words, an acronym
        the operator typed in capitals ("EXW", "OTIF"), or the one-word name of
        an Incoterm, KPI or document few items use. An everyday word ("buying",
        "stock", "sea") is not."""
        if len(name.split()) >= 2:
            return True
        if len(name) <= 6 and re.search(r"\b" + re.escape(name.upper()) + r"S?\b", str(query or "")):
            return True
        if name in name_keys(it.get("term")) or name == str(it.get("acronym") or "").lower():
            return True                   # the concept's own one-word name ("Demurrage")
        return it.get("kind") in ("incoterm", "kpi", "document") and self.name_df.get(name, 99) <= 5

    def current(self, it):
        return it.get("review_status", "unreviewed") in CURRENT

    def stale(self, it, today=None):
        """Past its review date: a country rule may have changed since."""
        due = it.get("review_by")
        return bool(due) and str(due) < (today or date.today().isoformat())

    def search(self, query, limit=5, domain=None, kind=None, scope=None, jurisdiction=None,
               include_history=False, approved_only=False):
        """
        The items that best answer `query`, best first:
        [{"id", "score", "item", "named", "flags"}].

        `named` is True when the question names the item outright (its term,
        acronym, a synonym or its Arabic term). Superseded and retired items
        are left out unless include_history; approved_only keeps approved ones.
        flags: unreviewed, stale, jurisdiction.
        """
        q = tokens(query)
        if not q:
            return []
        qs = " " + " | ".join(name_keys(query)) + " "
        named = set()
        for key, iids in self.names.items():
            if " " + key + " " in qs:
                named |= iids
        n = len(self.docs) or 1
        scored = []
        for iid, (tf, length) in self.docs.items():
            it = self.items[iid]
            if not include_history and not self.current(it):
                continue
            if approved_only and it.get("review_status") != "approved":
                continue
            if domain and it.get("domain") != domain:
                continue
            if kind and it.get("kind") != kind:
                continue
            if scope and it.get("scope") != scope:
                continue
            if jurisdiction and it.get("jurisdiction") not in (None, jurisdiction):
                continue
            s, hits = 0.0, 0
            for t in set(q):
                f = tf.get(t)
                if not f:
                    continue
                hits += 1
                df = self.df.get(t, 1)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * length / self.avg))
            name = None
            if iid in named:
                # Longer names said outright beat a word they share ("OTIF" over "on-time").
                name = max((k for k, v in self.names.items() if iid in v and " " + k + " " in qs),
                           key=lambda k: (len(k.split()), k))
                s += 12.0 + 3.0 * len(name.split())
                if len(name) <= 6 and len(name.split()) == 1 and \
                        re.search(r"\b" + re.escape(name.upper()) + r"S?\b", str(query or "")):
                    s += 6.0          # an acronym typed as such ("EXW") over an everyday word ("buying")
            if not s:
                continue
            s *= (0.5 + 0.5 * hits / len(set(q))) if iid not in named else 1.0
            flags = []
            if it.get("review_status") != "approved":
                flags.append(it.get("review_status", "unreviewed"))
            if self.stale(it):
                flags.append("stale")
            if it.get("scope") == "jurisdiction":
                flags.append("jurisdiction:" + str(it.get("jurisdiction")))
            # How much of the question the name is: 1.0 for "what is demurrage", low
            # for a generic word said in passing ("…proves who owns cargo at sea").
            cover = round(len(set(tokens(name)) & set(q)) / float(len(set(q))), 2) if name else 0.0
            specific = bool(name) and self._specific(name, it, query)
            scored.append({"id": iid, "score": round(s, 3), "item": it, "named": iid in named, "name": name,
                           "coverage": cover, "specific": specific, "flags": flags})
        scored.sort(key=lambda r: (-r["score"], r["id"]))
        return scored[:limit]

    def hit(self, iid, score, **extra):
        """A search result for one item, in search()'s shape (for candidates
        found another way, e.g. semantic retrieval)."""
        it = self.items[iid]
        flags = []
        if it.get("review_status") != "approved":
            flags.append(it.get("review_status", "unreviewed"))
        if self.stale(it):
            flags.append("stale")
        if it.get("scope") == "jurisdiction":
            flags.append("jurisdiction:" + str(it.get("jurisdiction")))
        return dict({"id": iid, "score": round(score, 3), "item": it, "named": False, "name": None,
                     "coverage": 0.0, "flags": flags}, **extra)

    def get(self, iid):
        return self.items.get(iid)

    def related(self, iid, depth=1):
        """Neighbours in the concept graph, both directions:
        [(id, relation, direction, depth)]."""
        out, seen, frontier = [], {iid}, [iid]
        for d in range(1, depth + 1):
            nxt = []
            for cur in frontier:
                it = self.items.get(cur) or {}
                for r in it.get("related") or []:
                    if r["id"] not in seen and r["id"] in self.items:
                        seen.add(r["id"])
                        out.append((r["id"], r.get("type", "related"), "out", d))
                        nxt.append(r["id"])
                for oid, rtype in self.incoming.get(cur, ()):
                    if oid not in seen:
                        seen.add(oid)
                        out.append((oid, rtype, "in", d))
                        nxt.append(oid)
            frontier = nxt
        return out

    def cite(self, it):
        """[{"title", "publisher", "url"|"path", "section", "page", "checked"}] for an item."""
        out = []
        for s in it.get("sources") or []:
            src = self.sources.get(s.get("source"))
            if not src:
                continue
            out.append({"id": src["id"], "title": src["title"], "publisher": src.get("publisher"),
                        "url": src.get("url"), "path": src.get("path"), "authority": src.get("authority"),
                        "section": s.get("section"), "page": s.get("page"), "note": s.get("note"),
                        "edition": src.get("edition"), "checked": src.get("checked")})
        return out

    def stats(self):
        by = lambda key: {k: sum(1 for it in self.items.values() if it.get(key) == k)
                          for k in sorted({str(it.get(key)) for it in self.items.values()})}
        return {"items": len(self.items), "errors": len(self.errors), "by_domain": by("domain"),
                "by_review": by("review_status"), "by_evidence": by("evidence_status"),
                "by_kind": by("kind"), "sources": len(self.sources),
                "stale": sum(1 for it in self.items.values() if self.stale(it))}


def kb():
    """The knowledge base, loaded once and reloaded when a file or a review changes."""
    sig = _signature()
    with _lock:
        if _cache["sig"] != sig or _cache["kb"] is None:
            _cache["kb"] = KB()
            _cache["sig"] = sig
        return _cache["kb"]


def reset():
    with _lock:
        _cache["sig"] = _cache["kb"] = None


def log_query(query, results, ms, mode, extra=None):
    """One line per question in the ATLAS store (kb_queries.jsonl): what was
    asked (redacted, cut), what came back, how long it took. For retrieval
    quality; never a record of who asked."""
    try:
        from . import store
        store.append("kb_queries.jsonl", dict({
            "at": store.now(), "q": store.redact(query, 200), "mode": mode, "ms": round(ms, 1),
            "top": [[r["id"], r["score"]] for r in results[:5]], "hit": bool(results)}, **(extra or {})))
    except Exception:
        pass


def search(query, **kw):
    t0 = time.perf_counter()
    res = kb().search(query, **kw)
    log_query(query, res, (time.perf_counter() - t0) * 1000, "search")
    return res
