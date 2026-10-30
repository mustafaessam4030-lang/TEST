"""
What a finished mission leaves behind, and how it is found again.

    record(mission, report)   the outcome and its lessons, as DRAFTS (never knowledge by themselves)
    similar(template, scope)  earlier missions about the same shipments / BUs / kind of problem
    promote(id, by)           a person sends a lesson to the knowledge review (kb_govern, as an observation)
    reject(id, by)

A model's conclusion never promotes itself: a lesson is a draft until a
person proposes it, and a proposal is knowledge only after a reviewer
approves it in the knowledge governance flow (kb_govern.py). Lessons come
only from hypotheses the records supported, with their evidence ids.
"""
import time
import uuid

from . import db


def record(m, rep):
    if not rep:
        return []
    scope = m.get("scope") or {}
    keys = {"template": m.get("template"), "refs": scope.get("refs") or [], "bus": scope.get("bus") or []}
    made = []
    mid = uuid.uuid4().hex[:12]
    supported = [h for h in rep.get("hypotheses") or [] if h.get("status") == "supported" and h.get("id") != "unknown"]
    text = "{0}: confidence {1}. {2}".format(m.get("title"), rep.get("grade"), rep.get("summary") or "")
    db.insert("memory", id=mid, mission_id=m["id"], kind="outcome", text=text[:600], keys=keys, evidence_ids=[],
              status="draft", created_at=time.time())
    made.append(mid)
    if rep.get("grade") not in ("Strong", "Moderate"):
        return made                                   # a weak or insufficient finding teaches nothing yet
    where = ", ".join(keys["refs"] or keys["bus"]) or "the records"
    for h in supported[:4]:
        lid = uuid.uuid4().hex[:12]
        why = "; ".join((h.get("why") or [])[:2])
        db.insert("memory", id=lid, mission_id=m["id"], kind="lesson",
                  text="In {0} (data as of the mission), \"{1}\" was supported by the records: {2}.".format(where, h["label"], why)[:600],
                  keys=dict(keys, hypothesis=h.get("id")), evidence_ids=[], status="draft", created_at=time.time())
        made.append(lid)
    db.event(m["id"], "memory", "{0} memory draft(s) kept; lessons become knowledge only through review".format(len(made)))
    return made


def similar(template, scope, exclude=None, limit=3):
    refs, bus = set((scope or {}).get("refs") or []), set((scope or {}).get("bus") or [])
    out = []
    for r in db.many("SELECT * FROM memory WHERE kind='outcome' ORDER BY created_at DESC LIMIT 400"):
        if r["mission_id"] == exclude:
            continue
        k = r.get("keys") or {}
        overlap = (refs & set(k.get("refs") or [])) or (not refs and bus & set(k.get("bus") or []))
        if overlap or (k.get("template") == template and (bus & set(k.get("bus") or []) or refs & set(k.get("refs") or []))):
            out.append({"mission_id": r["mission_id"], "text": r["text"], "at": r["created_at"],
                        "shared": sorted((refs & set(k.get("refs") or [])) | (bus & set(k.get("bus") or [])))})
        if len(out) >= limit:
            break
    return out


def listing(mission_id):
    return db.many("SELECT * FROM memory WHERE mission_id=? ORDER BY created_at", mission_id)


def promote(mem_id, by):
    """-> (ok, message). The lesson goes to the knowledge review queue as an observation."""
    r = db.one("SELECT * FROM memory WHERE id=?", mem_id)
    if not r or r["kind"] != "lesson":
        return False, "Only a lesson can be proposed as knowledge."
    if r["status"] != "draft":
        return False, "This lesson is already {0}.".format(r["status"].replace("_", " "))
    from intelligence import kb_govern
    try:
        p = kb_govern.propose(kind="observation", observation=r["text"], by=by,
                              note="From ATLAS mission {0}; a person proposed it, a reviewer decides.".format(r["mission_id"]))
    except Exception as error:
        return False, "The knowledge review refused it: {0}".format(str(error)[:160])
    db.update("memory", {"id": mem_id}, status="proposed_to_kb", promoted_by=str(by)[:80], proposal_id=p.get("proposal_id"))
    db.event(r["mission_id"], "memory", "{0} proposed a lesson for knowledge review ({1})".format(by, p.get("proposal_id")))
    return True, "Sent to knowledge review ({0}). It becomes knowledge only if a reviewer approves it.".format(p.get("proposal_id"))


def reject(mem_id, by):
    r = db.one("SELECT * FROM memory WHERE id=?", mem_id)
    if not r or r["status"] != "draft":
        return False, "Nothing to reject."
    db.update("memory", {"id": mem_id}, status="rejected", promoted_by=str(by)[:80])
    return True, "Lesson dropped."
