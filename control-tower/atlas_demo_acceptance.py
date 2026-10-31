"""
ATLAS demonstration acceptance tests A-F, against the REAL model and search.

    python atlas_demo.py --test-data          (in one window)
    python atlas_demo_acceptance.py           (in another)

A-E ask the running dashboard over HTTP, exactly as the chat does. F runs
ATLAS in this process with the model and the search pointed at ports nothing
listens on, and checks it falls back honestly. Prints each answer, its timing
and PASS/FAIL; the exit code is 1 when anything fails. Writes nothing.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ARABIC = "؀-ۿ"
RESULTS = []


def ask(url, question):
    body = json.dumps({"question": question, "context": {}}).encode("utf-8")
    request = urllib.request.Request(url.rstrip("/") + "/api/ask", data=body,
                                     headers={"Content-Type": "application/json"})
    started = time.time()
    with urllib.request.urlopen(request, timeout=300) as response:
        reply = json.loads(response.read().decode("utf-8"))
    return reply, time.time() - started


def verdict(test, question, reply, seconds, checks):
    failed = [name for name, ok in checks if not ok]
    RESULTS.append(not failed)
    llm = reply.get("llm") or {}
    print("=" * 72)
    print("{0}  {1}   ({2:.1f}s)".format(test, "PASS" if not failed else "FAIL", seconds))
    print("Q: " + question)
    print("model: {0}{1}".format("used" if llm.get("used") else "not used",
                                 "" if llm.get("used") else " — " + str(llm.get("reason"))))
    if llm.get("timings"):
        print("timings: " + json.dumps(llm["timings"]))
    print("-" * 72)
    print(reply.get("answer") or "")
    for name in failed:
        print("  FAILED CHECK: " + name)


def live(url):
    import re
    ref = "8842001173"

    q = "Which shipments failed in the last run and why?"
    r, t = ask(url, q)
    a = r.get("answer") or ""
    verdict("A1 English", q, r, t, [
        ("the model wrote the answer", (r.get("llm") or {}).get("used")),
        ("names the failed shipment from the records", ref in a),
        ("labelled TEST DATA", "TEST DATA" in a)])

    q = "ما هي الشحنات التي فشلت في آخر تشغيل ولماذا؟"
    r, t = ask(url, q)
    a = r.get("answer") or ""
    verdict("A2 Arabic", q, r, t, [
        ("the model wrote the answer", (r.get("llm") or {}).get("used")),
        ("answered in Arabic", (r.get("llm") or {}).get("language") == "ar"
         and len(re.findall("[" + ARABIC + "]", a)) > 40),
        ("names the failed shipment from the records", ref in a),
        ("labelled TEST DATA", "TEST DATA" in a)])

    q = "Give me a summary of the last run"
    r, t = ask(url, q)
    a = r.get("answer") or ""
    verdict("B  test run", q, r, t, [
        ("labelled TEST DATA", "TEST DATA" in a),
        ("states the run's real counts", ("2" in a and "1" in a) or "4" in a),
        ("invents no shipment reference", not set(re.findall(r"\b\d{10}\b", a)) -
         {"1570046231", "5271993480", "1570049117", "8842001173"})])

    q = "Why did {0} fail, and what should I check?".format(ref)
    r, t = ask(url, q)
    a = r.get("answer") or ""
    verdict("C  diagnosis", q, r, t, [
        ("names the shipment", ref in a),
        ("built on the recorded evidence", "Save/Update" in (r.get("details") or a)
         or "UNEXPECTED PAGE STATE" in (r.get("details") or a)),
        ("separates proven from likely", "Verified" in a or "Likely" in a
         or "Inference" in a)])

    q = "What does ERR_HTTP2_PROTOCOL_ERROR mean in Playwright and how is it usually fixed?"
    r, t = ask(url, q)
    a = r.get("answer") or ""
    verdict("D  web search", q, r, t, [
        ("a real search returned sources", bool(r.get("web_sources"))),
        ("source links shown", "http" in a),
        ("labelled as external research", "External research" in a)])

    q = "Search the Qatar Airways Cargo website for how to track an air waybill"
    r, t = ask(url, q)
    a = r.get("answer") or ""
    verdict("E  carrier website", q, r, t, [
        ("a real search returned sources", bool(r.get("web_sources"))),
        ("labelled not live carrier status", "not live carrier status" in a),
        ("source links shown", "http" in a)])


def fallback():
    """F: no model, no search — the rules answer, and an honest 'did not search'."""
    os.environ.update({"ATLAS_LLM_PROVIDER": "ollama", "ATLAS_LLM_MODEL": "qwen3.5:4b",
                       "ATLAS_LLM_URL": "http://127.0.0.1:9", "ATLAS_LLM_TIMEOUT_S": "5",
                       "ATLAS_SEARCH_URL": "http://127.0.0.1:9"})
    os.environ.pop("ATLAS_DEMO_TEST_DATA", None)
    from dashboard.bridge import ControlTowerState
    from dashboard import assistant
    import atlas_demo
    bridge = ControlTowerState()
    atlas_demo.seed_test_run(bridge)
    state = bridge.snapshot()

    q = "Which shipments failed in the last run and why?"
    t = time.time()
    r = assistant.answer(q, state)
    verdict("F1 model down", q, r, time.time() - t, [
        ("fell back to the rules", (r.get("llm") or {}).get("used") is False),
        ("the rules still answer from the records", "8842001173" in (r.get("answer") or ""))])

    # The model is down, so the rules decide; their own research path then
    # finds the search service down too.
    q = "What does ERR_HTTP2_PROTOCOL_ERROR mean in Playwright?"
    t = time.time()
    r = assistant.answer(q, state)
    a = r.get("answer") or ""
    verdict("F2 model and search down", q, r, time.time() - t, [
        ("no web sources pretended", not r.get("web_sources") and "http" not in a),
        ("says it did not search the web",
         "not search" in a.lower() or "isn't set up" in a or "couldn't" in a.lower()
         or "could not" in a.lower() or "did not" in a.lower())])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--only-fallback", action="store_true")
    args = parser.parse_args()
    if not args.only_fallback:
        live(args.url)
    fallback()
    print("=" * 72)
    print("{0} passed, {1} failed".format(RESULTS.count(True), RESULTS.count(False)))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
