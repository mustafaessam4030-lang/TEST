"""Reproducible evaluation over the synthetic samples.

    python scripts/run_eval.py                       # hashing embedder, extractive answers
    python scripts/run_eval.py --embedder fastembed  # needs requirements-optional.txt
    python scripts/run_eval.py --generator ollama --llm-model qwen2.5:7b-instruct

Each run ingests samples/ into a fresh temporary data directory, asks every
question in eval/questions.json, scores the results with deterministic checks
(no LLM judge), prints a summary and writes eval/results/<config>.json.
"""
from __future__ import annotations

import argparse
import json
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eka import citations as cite  # noqa: E402
from eka.config import load_settings  # noqa: E402
from eka.extract import extract  # noqa: E402
from eka.service import Assistant  # noqa: E402
from eka.textutil import compact  # noqa: E402

EXPECT_STATUS = {"answer": "answered", "abstain": "insufficient_evidence", "clarify": "needs_clarification",
                 "smalltalk": "smalltalk"}


def text_of(claims) -> str:
    return "\n".join(c["text"] for c in claims)


def contains(haystack: str, needle: str) -> bool:
    return compact(needle) in compact(haystack)


def evaluate_ingestion(assistant, spec, samples) -> dict:
    docs = {d["filename"]: d for d in assistant.store.documents()}
    rows, strings_total, strings_found, status_ok, pages_ok, pages_total = [], 0, 0, 0, 0, 0
    for item in spec:
        doc = docs[item["file"]]
        pages = [p["status"] for p in assistant.store.pages(doc["id"])]
        status_ok += doc["status"] == item["status"]
        pages_total += len(item["pages"])
        pages_ok += sum(a == b for a, b in zip(pages, item["pages"]))
        ex = None
        if item["contains"] or item.get("order"):
            ex = extract((samples / item["file"]).read_bytes(), assistant.settings)
        missing = []
        for page, needles in item["contains"].items():
            page_text = page_text_of(ex, int(page))
            for needle in needles:
                strings_total += 1
                if contains(page_text, needle):
                    strings_found += 1
                else:
                    missing.append("p{0}: {1}".format(page, needle))
        order_ok = True
        for page, sequence in (item.get("order") or {}).items():
            text = page_text_of(ex, int(page))
            positions = [text.find(s) for s in sequence]
            order_ok &= all(p >= 0 for p in positions) and positions == sorted(positions)
        rows.append({"file": item["file"], "status": doc["status"], "expected_status": item["status"],
                     "page_statuses": pages, "missing_strings": missing, "reading_order_ok": order_ok,
                     "error": doc["error"][:160]})
    return {"documents": rows, "status_accuracy": status_ok / len(spec),
            "page_status_accuracy": pages_ok / max(pages_total, 1),
            "key_string_recall": strings_found / max(strings_total, 1),
            "key_strings": [strings_found, strings_total]}


def page_text_of(ex, page_no: int) -> str:
    page = ex.pages[page_no - 1]
    parts = []
    for e in page.elements:
        parts.append(e.text if e.kind != "table" else "\n".join(
            " | ".join("{0}: {1}".format(h, v) for h, v in zip(e.header, row)) for row in e.rows))
    return "\n".join(parts)


def evaluate_questions(assistant, questions) -> tuple[list[dict], dict]:
    docs = {d["filename"]: d for d in assistant.store.documents()}
    results, latencies = [], []
    for q in questions:
        cid, answer, error = None, None, ""
        try:
            for turn in q["turns"]:
                started = time.perf_counter()
                answer = assistant.engine.ask(turn, cid)
                latencies.append((time.perf_counter() - started) * 1000)
                cid = answer["conversation_id"]
        except Exception as exc:  # counted as a failure, never hidden
            error = repr(exc)
        r = {"id": q["id"], "category": q["category"], "expect": q["expect"], "error": error}
        if answer is None:
            results.append(dict(r, status="exception", behaviour_ok=False, correct=False))
            continue
        status = answer["status"]
        r["status"] = status
        r["answer_text"] = answer["answer_text"]
        r["behaviour_ok"] = status == EXPECT_STATUS[q["expect"]]
        everything = text_of(answer["claims"]) + "\n" + "\n".join(text_of(c["claims"]) for c in answer["conflicts"])
        main = text_of([c for c in answer["claims"] if c["kind"] != "superseded"])
        includes = all(contains(everything, s) for s in q.get("must_include", []))
        excludes = not any(contains(main, s) for s in q.get("must_not_include", []))
        r["correct"] = r["behaviour_ok"] and includes and excludes
        r["missing"] = [s for s in q.get("must_include", []) if not contains(everything, s)]

        # Citations: validity, unsupported values, expected page hit and precision.
        valid, problems = cite.validate(answer["citations"], assistant.store)
        r["citations"] = len(answer["citations"])
        r["invalid_citations"] = len(problems)
        by_id = {c["id"]: c for c in answer["citations"]}
        unsupported = 0
        claims = answer["claims"] + [c for x in answer["conflicts"] for c in x["claims"]]
        for claim in claims:
            sources = [assistant.store.chunk(by_id[x]["chunk_id"])["search_text"]
                       for x in claim["citations"] if x in by_id]
            if not sources or cite.unsupported_values(claim["text"], sources):
                unsupported += 1
        r["claims"], r["unsupported_claims"] = len(claims), unsupported
        expected_docs = [docs[d]["id"] for d in ([q["doc"]] if q.get("doc") else q.get("docs", []))]
        if expected_docs and q["expect"] == "answer":
            pages = set(q.get("pages", []))
            ok = lambda doc_id, page: doc_id in expected_docs and (not pages or page in pages)
            main_cites = {x for c in answer["claims"] if c["kind"] == "direct" for x in c["citations"]}
            cited = [by_id[x] for x in main_cites if x in by_id]
            r["citation_hit"] = any(ok(c["doc_id"], c["page"]) for c in cited)
            r["citation_precision"] = (sum(ok(c["doc_id"], c["page"]) for c in cited) / len(cited)) if cited else 0.0
            selected = answer["trace"].get("selected", [])
            ranks = [i for i, s in enumerate(selected) if ok(s["doc_id"], s["page"])]
            r["retrieval_hit"] = bool(ranks)
            r["reciprocal_rank"] = 1.0 / (ranks[0] + 1) if ranks else 0.0
        results.append(r)
    return results, {"p50": statistics.median(latencies), "p95": percentile(latencies, 95),
                     "max": max(latencies), "turns": len(latencies)}


def percentile(values, pct):
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def summarise(results) -> dict:
    answerable = [r for r in results if r["expect"] == "answer"]
    unsupported_q = [r for r in results if r["expect"] == "abstain"]
    with_cites = [r for r in answerable if "citation_hit" in r]
    total_claims = sum(r.get("claims", 0) for r in results)
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    return {
        "questions": len(results),
        "behaviour_accuracy": mean([r["behaviour_ok"] for r in results]),
        "answer_correctness": mean([r["correct"] for r in results]),
        "answerable_correct": mean([r["correct"] for r in answerable]),
        "retrieval_recall_at_k": mean([r.get("retrieval_hit", False) for r in with_cites]),
        "retrieval_mrr": mean([r.get("reciprocal_rank", 0.0) for r in with_cites]),
        "citation_hit_rate": mean([r.get("citation_hit", False) for r in with_cites]),
        "citation_precision": mean([r.get("citation_precision", 0.0) for r in with_cites if r["status"] == "answered"]),
        "invalid_citations": sum(r.get("invalid_citations", 0) for r in results),
        "claims": total_claims,
        "unsupported_claim_rate": (sum(r.get("unsupported_claims", 0) for r in results) / total_claims) if total_claims else 0.0,
        "abstention_recall": mean([r["status"] == "insufficient_evidence" for r in unsupported_q]),
        "false_abstention_rate": mean([r["status"] == "insufficient_evidence" for r in answerable]),
        "failures": sum(1 for r in results if r["error"] or r["status"] in ("error", "exception")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--embedder", default="hashing")
    parser.add_argument("--generator", default="extractive")
    parser.add_argument("--llm-model", default="qwen2.5:7b-instruct")
    parser.add_argument("--samples", type=Path, default=ROOT / "samples")
    parser.add_argument("--questions", type=Path, default=ROOT / "eval" / "questions.json")
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "results")
    args = parser.parse_args()
    spec = json.loads(args.questions.read_text())

    with tempfile.TemporaryDirectory(prefix="eka-eval-") as tmp:
        settings = load_settings(data_dir=tmp, embedder=args.embedder, generator=args.generator,
                                 llm_model=args.llm_model)
        started = time.perf_counter()
        assistant = Assistant(settings)
        load_s = time.perf_counter() - started
        ingest_times = {}
        for path in sorted(args.samples.glob("*.pdf")):
            t = time.perf_counter()
            assistant.ingest.submit(path.read_bytes(), path.name)
            assistant.ingest.wait_idle(600)
            ingest_times[path.name] = round(time.perf_counter() - t, 2)
        dup_doc, duplicate = assistant.ingest.submit((args.samples / "CV-90_Gearbox_Datasheet.pdf").read_bytes(), "dup.pdf")
        ingestion = evaluate_ingestion(assistant, spec["ingestion"], args.samples) if spec["ingestion"] else \
            {"status_accuracy": 0, "page_status_accuracy": 0, "key_strings": [0, 0], "documents": []}
        ingestion["duplicate_detected"] = duplicate
        ingestion["seconds_per_file"] = ingest_times
        results, latency = evaluate_questions(assistant, spec["questions"])
        summary = summarise(results)
        assistant.close()

    config = "{0}-{1}".format(args.embedder, args.generator if args.generator == "extractive"
                              else args.generator + "-" + args.llm_model.replace(":", "_"))
    if args.questions.stem != "questions":
        config += "-" + args.questions.stem
    report = {"config": config, "embedder": settings.embedder, "generator": settings.generator,
              "summary": summary, "latency_ms": latency, "ingestion": ingestion,
              "startup_s": round(load_s, 2),
              "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
              "results": results}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / (config + ".json")).write_text(json.dumps(report, indent=2))

    print("config:", config)
    for key, value in summary.items():
        print("  {0:24s} {1}".format(key, round(value, 3) if isinstance(value, float) else value))
    print("  latency ms               p50 {p50:.1f}  p95 {p95:.1f}  max {max:.1f}".format(**latency))
    if spec["ingestion"]:
        print("  ingestion                status {0:.0%}  page status {1:.0%}  key strings {2}/{3}  duplicate {4}".format(
            ingestion["status_accuracy"], ingestion["page_status_accuracy"], *ingestion["key_strings"],
            ingestion["duplicate_detected"]))
    print("  peak RSS MB             ", report["peak_rss_mb"])
    for r in results:
        if not r["correct"]:
            print("  MISS {0:10s} expected {1:9s} got {2:22s} missing {3}".format(
                r["id"], r["expect"], r["status"], r.get("missing")))


if __name__ == "__main__":
    main()
