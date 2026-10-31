"""
The Commander's planning: which graph a mission runs, checked before it runs.

    draft(assessment, profile, mission_id)   template first; a planning model may only pick another
                                             catalogued template or skip optional tasks (validated)
    build(template, items=None, remove=())   -> [task]; a fan-out template gets one task per item (<= 25)
    edit_ok(template, remove)                -> the tasks a person may remove (REQUIRED stay)
    validate(tasks)                          -> [problem]; empty when the graph is safe to run

A plan names only registered specialists (registry.SPECIALISTS) and only
catalogued templates; anything else is rejected and the template plan is used,
with a note. The graph must be acyclic, every dependency must exist, and it
stays inside the size limits (tasks, distinct agents, fan-out items).
"""
import json
import os
import re

from . import models
from .registry import REQUIRED, SPECIALISTS, TEMPLATES

MAX_TASKS = int(os.environ.get("ATLAS_MISSION_MAX_TASKS") or 60)
MAX_AGENTS = int(os.environ.get("ATLAS_MISSION_MAX_AGENTS") or 12)
FANOUT_MAX = 25
# Mission deadlines, minutes (configurable per mission when it is approved)
DEADLINE_MIN = {"ADVANCED": 10, "DEEP_MISSION": 15, "CRITICAL_INCIDENT": 30}


def build(template, items=None, remove=()):
    """The task list: [{id, specialist, objective, depends_on, params}]."""
    t = TEMPLATES[template]
    fan = t.get("fanout")
    remove = set(remove or ()) - set(REQUIRED)
    out = []
    item_ids = []
    if fan:
        for i, it in enumerate((items or [])[:min(fan.get("max", FANOUT_MAX), FANOUT_MAX)]):
            item_ids.append("i{0:02d}".format(i + 1))
    removed = {}
    for tid, spec, objective, deps in t["tasks"]:
        if tid in remove:
            removed[tid] = deps
    for tid, spec, objective, deps in t["tasks"]:
        if tid in remove:
            continue
        resolved = []
        stack = list(deps)
        while stack:                                   # a removed task's own dependencies take its place
            d = stack.pop(0)
            if d in removed:
                stack.extend(removed[d])
            elif d not in resolved:
                resolved.append(d)
        if fan and tid == fan["task"]:
            for i, iid in enumerate(item_ids):
                out.append({"id": iid, "specialist": spec, "objective": "{0}: {1}".format(objective.split(" (")[0], items[i]),
                            "depends_on": resolved, "params": {"awb": items[i]}})
            continue
        if fan and fan["task"] in resolved:
            resolved = [d for d in resolved if d != fan["task"]] + item_ids
        out.append({"id": tid, "specialist": spec, "objective": objective, "depends_on": resolved, "params": {}})
    return out


def validate(tasks):
    problems = []
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        problems.append("duplicate task ids")
    known = set(ids)
    for t in tasks:
        if t["specialist"] not in SPECIALISTS:
            problems.append("unknown specialist: {0}".format(t["specialist"]))
        for d in t.get("depends_on") or []:
            if d not in known:
                problems.append("{0} depends on a task that is not in the plan: {1}".format(t["id"], d))
    if len(tasks) > MAX_TASKS:
        problems.append("{0} tasks: more than the limit of {1}".format(len(tasks), MAX_TASKS))
    agents = {t["specialist"] for t in tasks}
    if len(agents) > MAX_AGENTS:
        problems.append("{0} different agents: more than the limit of {1}".format(len(agents), MAX_AGENTS))
    # cycle check (Kahn)
    deps = {t["id"]: set(d for d in (t.get("depends_on") or []) if d in known) for t in tasks}
    ready = [k for k, v in deps.items() if not v]
    seen = 0
    while ready:
        k = ready.pop()
        seen += 1
        for other, v in deps.items():
            if k in v:
                v.discard(k)
                if not v:
                    ready.append(other)
    if seen != len(tasks):
        problems.append("the plan has a cycle")
    if not any(t["specialist"] == "report" for t in tasks):
        problems.append("the plan has no report")
    return problems


def removable(template):
    return [tid for tid, _, _, _ in TEMPLATES[template]["tasks"] if tid not in REQUIRED]


PLANNER = ("You plan investigations for a logistics control tower. Choose ONE template from this list and, "
           "optionally, optional tasks to skip. Reply JSON only: {\"template\": \"<id>\", \"skip\": [\"<task id>\"], "
           "\"why\": \"<one sentence>\"}. You may not invent templates, tasks, tools or agents.")


def draft(a, profile, mission_id=None):
    """-> {"template", "skip", "note", "model"}. Templates first: a planning model is asked only when the
    owner allows it (ATLAS_MISSION_PLANNER=1) and the profile is not a drill. Its answer is validated
    against the catalogue; anything outside it is rejected and the classifier's template stands."""
    out = {"template": a["template"], "skip": [], "note": "template chosen from the request's words", "model": None}
    if profile == "drill" or str(os.environ.get("ATLAS_MISSION_PLANNER") or "0").strip().lower() not in ("1", "on", "yes", "true"):
        return out
    menu = {k: {"title": v["title"], "optional": removable(k)} for k, v in TEMPLATES.items()
            if not v.get("scope_free") or not (a["scope"].get("refs") or a["scope"].get("bus"))}
    try:
        r = models.call("plan", PLANNER, "REQUEST: {0}\nSCOPE: {1}\nTEMPLATES: {2}".format(
            a["question"], json.dumps({"refs": a["scope"].get("refs"), "bus": a["scope"].get("bus")}), json.dumps(menu)),
            mission_id=mission_id, purpose="mission plan", profile=profile, max_tokens=160, json_mode=True)
    except Exception as error:
        out["note"] = "no planning model available ({0}); template plan".format(type(error).__name__)
        return out
    out["model"] = r["model"]
    try:
        m = re.search(r"\{.*\}", r["text"], re.S)
        v = json.loads(m.group(0)) if m else {}
    except ValueError:
        v = {}
    tpl, skip = v.get("template"), v.get("skip") or []
    if tpl not in menu:
        out["note"] = "{0} named a template that is not in the catalogue ({1}); rejected, template plan used".format(r["model"], str(tpl)[:40])
        return out
    if not isinstance(skip, list) or any(s not in removable(tpl) for s in skip):
        out["note"] = "{0} asked to skip tasks that are required or unknown; rejected, template plan used".format(r["model"])
        return out
    out.update(template=tpl, skip=list(skip), note="planned by {0}: {1}".format(r["model"], str(v.get("why") or "")[:160]))
    return out
