"""
ATLAS's work list — every failure, with its plan, until it is resolved.

The automation never stops for a failure: the shipment ends, the run moves
on, and the failure lands here with what ATLAS knows and what will be done
about it (intelligence/failures.work_mode):

    RETRY_THIS_RUN   retried once by the run, after the other shipments
    NEXT_RUN         looked up again by the next run, by itself
    NEEDS_PERSON     a person's step (Open & Continue)
    NEEDS_DECISION   an owner decides (a rule, a write, an unknown cause)

An item is RESOLVED only by verification: a later outcome for the same
shipment that was written AND read back. A success that was not read back
is noted and the item stays open. Operator feedback never resolves an
item, and ATLAS never closes one by itself; a person may close it with a
note (python -m intelligence.backlog close KEY --by NAME --note TEXT).

One item per shipment and failure class, keyed provider:category:reference.
Stored as recovery_backlog.json in the intelligence store, labelled with
the store's origin like everything else there. Standard library only;
never raises into the automation.
"""

import threading

from . import store

FILE = "recovery_backlog.json"
MAX_ITEMS = 2000
MAX_HISTORY = 20

OPEN_STATES = ("RETRY_QUEUED", "RETRYING", "RETRY_FAILED", "AWAITING_VERIFICATION", "OPEN",
               "NEEDS_PERSON", "NEEDS_DECISION")
DONE_STATES = ("RESOLVED_VERIFIED", "CLOSED")
STATUS_OF_MODE = {"RETRY_THIS_RUN": "RETRY_QUEUED", "NEXT_RUN": "OPEN",
                  "NEEDS_PERSON": "NEEDS_PERSON", "NEEDS_DECISION": "NEEDS_DECISION"}
WORDS = {
    "RETRY_QUEUED": "queued for one retry later in this run",
    "RETRYING": "being retried now",
    "RETRY_FAILED": "retried this run, failed again; the next run looks it up again",
    "AWAITING_VERIFICATION": "written, but the Hub read-back did not confirm it; stays open",
    "OPEN": "the next run looks it up again by itself",
    "NEEDS_PERSON": "needs a person (Open & Continue)",
    "NEEDS_DECISION": "needs a decision from the automation's owner",
    "RESOLVED_VERIFIED": "resolved — written and read back",
    "CLOSED": "closed by a person",
}

_lock = threading.Lock()


def key_of(provider, category, reference):
    return "{0}:{1}:{2}".format(str(provider or "UNKNOWN").upper(),
                                str(category or "UNKNOWN_FAILURE").upper(), reference)


def _load():
    data = store.load_json(FILE, {})
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        data = {"items": {}}
    return data


def _save(data):
    # The store's origin decides whether this process may write to it: a
    # test never writes into a production store, nor the other way round.
    if not store._admitted():
        return False
    items = data["items"]
    if len(items) > MAX_ITEMS:
        keep = sorted(items.values(), key=lambda i: (i["status"] in DONE_STATES,
                                                     -float(i.get("updated_epoch") or 0)))
        data["items"] = {i["key"]: i for i in keep[:MAX_ITEMS]}
    return store.save_json(FILE, data)


def _note(item, event, run_id, detail):
    item["history"] = (item.get("history") or [])[-(MAX_HISTORY - 1):] + [
        {"at": store.stamp(), "run_id": run_id, "event": event, "detail": detail}]
    item["updated"] = store.stamp()
    item["updated_epoch"] = round(store.now(), 1)


def add(failure):
    """Record one failure record (intelligence/failures.from_record) on the list."""
    try:
        work = failure.get("work") or {}
        mode = work.get("mode") or "NEEDS_DECISION"
        key = key_of(failure.get("provider") or failure.get("carrier"),
                     failure.get("classification"), failure.get("shipment_id"))
        plan = failure.get("recovery_plan") or {}
        with _lock:
            data = _load()
            item = data["items"].get(key)
            fresh = item is None or item["status"] in DONE_STATES
            if item is None:
                item = {"key": key, "reference": failure.get("shipment_id"),
                        "carrier": failure.get("carrier"), "provider": failure.get("provider"),
                        "classification": failure.get("classification"),
                        "first_seen": store.stamp(), "occurrences": 0, "runs": []}
                data["items"][key] = item
            run_id = failure.get("run_id")
            same_failure = item.get("last_failure_id") == failure.get("failure_id")
            if not same_failure:
                item["occurrences"] = int(item.get("occurrences") or 0) + 1
            if run_id and run_id not in item["runs"]:
                item["runs"] = (item["runs"] + [run_id])[-20:]
            status = STATUS_OF_MODE.get(mode, "NEEDS_DECISION")
            if work.get("retried") and mode == "RETRY_THIS_RUN":
                status = "RETRY_FAILED"
            item.update(
                label=failure.get("classification_label"), stage=failure.get("stage_label"),
                last_seen=store.stamp(), last_run_id=run_id,
                last_failure_id=failure.get("failure_id"), mode=mode, why=work.get("why"),
                status=status, headline=failure.get("headline"),
                root_cause_status=failure.get("root_cause_status"),
                plan_status=plan.get("status"), plan_statement=plan.get("statement"),
                next_steps=[{"strategy": s.get("strategy"), "source": s.get("source"),
                             "executes": s.get("executes")}
                            for s in (plan.get("steps") or [])[:4]],
                recommendations=[r.get("text") for r in failure.get("recommendations") or []][:3],
                resolved=None)
            if fresh or not same_failure:
                _note(item, "reopened" if fresh and item["occurrences"] > 1 else "added",
                      run_id, "{0} — {1}".format(failure.get("classification"), WORDS[status]))
            elif status == "RETRY_FAILED":
                _note(item, "retry_failed", run_id, WORDS[status])
            _save(data)
            return dict(item)
    except Exception:
        return None


def retrying(reference, run_id):
    """The run picked this shipment up again for its one deferred retry."""
    return _move(reference, run_id, ("RETRY_QUEUED",), "RETRYING", "retry_started",
                 "retried once, after the other shipments")


def outcome(reference, run_id, result, verified):
    """
    A later outcome for this shipment. Only a VERIFIED success resolves its
    open items; an unverified success is noted and the items stay open.
    """
    try:
        with _lock:
            data = _load()
            changed = []
            for item in data["items"].values():
                if item.get("reference") != reference or item["status"] in DONE_STATES:
                    continue
                if result == "SUCCESS" and verified:
                    item["status"] = "RESOLVED_VERIFIED"
                    item["resolved"] = {"at": store.stamp(), "run_id": run_id,
                                        "by": "verification: written and read back"}
                    _note(item, "resolved", run_id, WORDS["RESOLVED_VERIFIED"])
                    changed.append(dict(item))
                elif result == "SUCCESS":
                    item["status"] = "AWAITING_VERIFICATION"
                    _note(item, "success_unverified", run_id, WORDS["AWAITING_VERIFICATION"])
                    changed.append(dict(item))
            if changed:
                _save(data)
            return changed
    except Exception:
        return []


def close(key, by, note=""):
    """A person closes an item. Recorded with who and why; never automatic."""
    if not by:
        return None
    with _lock:
        data = _load()
        item = data["items"].get(key)
        if item is None:
            return None
        item["status"] = "CLOSED"
        item["resolved"] = {"at": store.stamp(), "by": "closed by {0}".format(by),
                            "note": note}
        _note(item, "closed", None, "closed by {0}{1}".format(by, ": " + note if note else ""))
        return dict(item) if _save(data) else None


def _move(reference, run_id, from_states, to_state, event, detail):
    try:
        with _lock:
            data = _load()
            moved = []
            for item in data["items"].values():
                if item.get("reference") == reference and item["status"] in from_states:
                    item["status"] = to_state
                    _note(item, event, run_id, detail)
                    moved.append(dict(item))
            if moved:
                _save(data)
            return moved
    except Exception:
        return []


def items(status=None, open_only=False, reference=None, limit=200):
    """Newest first."""
    rows = list(_load()["items"].values())
    if open_only:
        rows = [r for r in rows if r["status"] in OPEN_STATES]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if reference:
        rows = [r for r in rows if r.get("reference") == reference]
    rows.sort(key=lambda r: -float(r.get("updated_epoch") or 0))
    return rows[:limit]


def summary():
    counts = {}
    for item in _load()["items"].values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"open": sum(counts.get(s, 0) for s in OPEN_STATES),
            "resolved": counts.get("RESOLVED_VERIFIED", 0),
            "closed": counts.get("CLOSED", 0), "by_status": counts}


def main(argv=None):                                      # pragma: no cover
    import argparse
    import json
    parser = argparse.ArgumentParser(description="ATLAS's recovery work list")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list")
    shut = sub.add_parser("close")
    shut.add_argument("key")
    shut.add_argument("--by", required=True)
    shut.add_argument("--note", default="")
    args = parser.parse_args(argv)
    if args.cmd == "close":
        print(json.dumps(close(args.key, args.by, args.note), indent=2))
    else:
        for item in items(open_only=True):
            print("{0:<48} {1:<16} {2}".format(item["key"], item["status"], item.get("headline")))
        print(json.dumps(summary()))


if __name__ == "__main__":                                # pragma: no cover
    main()
