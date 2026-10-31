"""
ATLAS load test: how many questions at once, with the real local model.

Runs ONLY against the labelled test run — never the production tower:

    python atlas_demo.py --test-data --port 8790          (window 1)
    python atlas_load_test.py 1  results_1.json           (window 2; then 5, then 10)

    level 1 = 5 representative questions, one after another
    level 5 = the same 5 at the same instant
    level 10 = those 5 + 5 similar ones at the same instant

Run the levels one after another, never overlapping. To try the optional
model gate (intelligence/converse.py), start the demo with e.g.
ATLAS_LLM_SLOTS=1 ATLAS_LLM_QUEUE=2 ATLAS_LLM_WAIT_S=150 set.

Measures per request: HTTP outcome, latency, whether the real model answered
(llm.used) or the rules fallback did (and why), and whether the answer belongs
to its own question. Meanwhile, every second: CPU %, RAM, the model runner's
memory, and the dashboard's own /api/state latency.
"""
import json
import re
import statistics
import sys
import threading
import time
import urllib.request

import os
BASE = os.environ.get("ATLAS_LOAD_TEST_URL") or "http://127.0.0.1:8790"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
REFS = ["1570046231", "5271993480", "1570049117", "8842001173"]

# (id, question, kind, own marker the model's restatement must keep)
CORE = [
    ("Q1", "Where is shipment 1570046231?", "shipment", "1570046231"),
    ("Q2", "Why did 8842001173 fail?", "failure", "8842001173"),
    ("Q3", "Give me a summary of the last run", "summary", None),
    ("Q4", "im so sad", "chat", None),
    ("Q5", "What does ERR_HTTP2_PROTOCOL_ERROR mean in Playwright?", "web", "ERR_HTTP2"),
]
EXTRA = [
    ("Q6", "What is the ETA for 5271993480?", "shipment", "5271993480"),
    ("Q7", "Why was 1570049117 skipped?", "failure", "1570049117"),
    ("Q8", "Compare the carriers in this run", "summary", None),
    ("Q9", "thanks atlas, you are great", "chat", None),
    ("Q10", "What is an air waybill number?", "web", None),
]


def ask(qid, question, results, start_gate):
    body = json.dumps({"question": question,
                       "context": {"progress_id": "bench-" + qid + "-" + str(time.time_ns())}
                       }).encode()
    request = urllib.request.Request(BASE + "/api/ask", data=body,
                                     headers={"Content-Type": "application/json"})
    start_gate.wait()
    t0 = time.time()
    record = {"id": qid, "question": question, "start": t0}
    try:
        with OPENER.open(request, timeout=900) as response:
            reply = json.loads(response.read().decode("utf-8"))
        record.update(http="ok", reply=reply)
    except Exception as error:
        record.update(http="error", error="{0}: {1}".format(type(error).__name__, error))
    record["end"] = time.time()
    record["latency"] = record["end"] - t0
    results[qid] = record


class Monitor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.samples, self.dash = [], []

    @staticmethod
    def _cpu():
        f = open("/proc/stat").readline().split()[1:]
        v = list(map(int, f))
        return sum(v), v[3] + v[4]

    @staticmethod
    def _runner_rss_mb():
        import os
        total = 0
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                cmd = open("/proc/%s/cmdline" % pid, "rb").read()
                if b"llama-server" in cmd or b"ollama\x00runner" in cmd:
                    for line in open("/proc/%s/status" % pid):
                        if line.startswith("VmRSS"):
                            total += int(line.split()[1]) / 1024
            except OSError:
                pass
        return total

    def run(self):
        last = self._cpu()
        while not self.stop.is_set():
            # dashboard responsiveness: the endpoint its page polls
            t = time.time()
            try:
                OPENER.open(BASE + "/api/state", timeout=10).read()
                self.dash.append(("ok", time.time() - t))
            except Exception as error:
                self.dash.append(("error", time.time() - t))
            time.sleep(1)
            now = self._cpu()
            total, idle = now[0] - last[0], now[1] - last[1]
            last = now
            mem = dict(l.split(":", 1) for l in open("/proc/meminfo"))
            used = (int(mem["MemTotal"].split()[0]) - int(mem["MemAvailable"].split()[0])) / 1024
            self.samples.append({"t": time.time(), "cpu": 100.0 * (total - idle) / max(total, 1),
                                 "ram_mb": used, "runner_mb": self._runner_rss_mb()})


def pct(values, p):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def check(record, kind, marker, all_questions):
    """Does this answer belong to this request? (and nothing from another)"""
    reply = record.get("reply") or {}
    llm = reply.get("llm") or {}
    q_en = (llm.get("question_en") or "")
    answer = reply.get("answer") or ""
    problems = []
    if llm.get("used"):
        if marker and marker not in q_en and marker not in answer:
            problems.append("own marker {0} missing".format(marker))
        # another request's distinctive reference in this one's restatement
        for ref in REFS:
            if ref in q_en and ref not in record["question"]:
                problems.append("foreign reference {0} in the restatement".format(ref))
        if "ERR_HTTP2" in q_en and "ERR_HTTP2" not in record["question"]:
            problems.append("another request's error text in the restatement")
        if kind == "chat" and llm.get("mode") != "chat":
            problems.append("chat answered as a {0}".format(llm.get("mode")))
        if kind != "chat" and llm.get("mode") == "chat":
            problems.append("question answered as chat")
    if "TEST DATA" not in answer and record.get("http") == "ok":
        problems.append("TEST DATA label missing")
    return problems


def main():
    level, out = int(sys.argv[1]), sys.argv[2]
    questions = CORE if level in (1, 5) else CORE + EXTRA
    kinds = {q[0]: (q[2], q[3]) for q in questions}
    monitor = Monitor()
    monitor.start()
    time.sleep(3)                                     # idle baseline samples
    results = {}
    t_start = time.time()
    if level == 1:
        for qid, question, _k, _m in questions:
            gate = threading.Event()
            gate.set()
            ask(qid, question, results, gate)
    else:
        gate = threading.Event()
        threads = [threading.Thread(target=ask, args=(qid, q, results, gate))
                   for qid, q, _k, _m in questions]
        for th in threads:
            th.start()
        time.sleep(0.2)
        gate.set()                                    # all at the same instant
        for th in threads:
            th.join()
    t_total = time.time() - t_start
    time.sleep(2)
    monitor.stop.set()
    monitor.join()

    rows = []
    for qid, _q, kind, marker in questions:
        r = results[qid]
        llm = (r.get("reply") or {}).get("llm") or {}
        reason = llm.get("reason") or ""
        rows.append({
            "id": qid, "kind": kind, "latency_s": round(r["latency"], 1), "http": r["http"],
            "model_used": bool(llm.get("used")), "mode": llm.get("mode"),
            "fallback_reason": reason[:120], "timed_out": "timed out" in reason,
            "timings": llm.get("timings"), "question_en": llm.get("question_en"),
            "web_sources": len((r.get("reply") or {}).get("web_sources") or []),
            "problems": check(r, kind, marker, questions),
            "answer_head": ((r.get("reply") or {}).get("answer") or r.get("error") or "")[:220],
        })
    lat = [r["latency_s"] for r in rows]
    busy = [s for s in monitor.samples if t_start <= s["t"] <= t_start + t_total]
    dash_ok = [d for s, d in monitor.dash if s == "ok"]
    summary = {
        "level": level, "requests": len(rows),
        "http_ok": sum(r["http"] == "ok" for r in rows),
        "http_errors": sum(r["http"] != "ok" for r in rows),
        "model_answers": sum(r["model_used"] for r in rows),
        "fallbacks": sum(not r["model_used"] for r in rows),
        "fallback_timeouts": sum(r["timed_out"] for r in rows),
        "correctness_problems": sum(bool(r["problems"]) for r in rows),
        "total_s": round(t_total, 1),
        "throughput_rps": round(len(rows) / t_total, 4),
        "answers_per_min": round(60 * len(rows) / t_total, 2),
        "lat_avg": round(statistics.mean(lat), 1), "lat_median": round(statistics.median(lat), 1),
        "lat_p95": round(pct(lat, 95), 1), "lat_min": min(lat), "lat_max": max(lat),
        "cpu_avg": round(statistics.mean(s["cpu"] for s in busy), 1) if busy else None,
        "cpu_max": round(max(s["cpu"] for s in busy), 1) if busy else None,
        "ram_max_mb": round(max(s["ram_mb"] for s in busy)) if busy else None,
        "runner_max_mb": round(max(s["runner_mb"] for s in busy)) if busy else None,
        "dash_checks": len(monitor.dash), "dash_errors": sum(s != "ok" for s, _ in monitor.dash),
        "dash_median_ms": round(1000 * statistics.median(dash_ok)) if dash_ok else None,
        "dash_p95_ms": round(1000 * pct(dash_ok, 95)) if dash_ok else None,
        "dash_max_ms": round(1000 * max(dash_ok)) if dash_ok else None,
    }
    json.dump({"summary": summary, "rows": rows}, open(out, "w"), indent=1)
    print(json.dumps(summary, indent=1))
    for r in rows:
        print("{id:>4} {kind:<8} {latency_s:>6}s model={model_used!s:<5} {mode!s:<12} "
              "{fallback_reason} {problems}".format(**r))


if __name__ == "__main__":
    main()
