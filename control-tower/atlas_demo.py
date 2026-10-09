"""
ATLAS demonstration launcher: the dashboard with ATLAS in conversation.

    python atlas_demo.py --check                 what is ready, what is not (changes nothing)
    python atlas_demo.py --replay                the last REAL run, from C:\\Automation
    python atlas_demo.py --test-data             a labelled TEST run, for a machine with no run

It starts the dashboard on http://127.0.0.1:8787 only (this machine), with
learning off. It never starts the automation, never signs in to the Hub or a
carrier, and never sends email. ATLAS reads the run snapshot; it cannot act on
anything (dashboard/server.py hands it a copy).

Needs, for conversation:  Ollama running, with the model pulled
                              ollama pull qwen3.5:4b
Needs, for web research:  a self-hosted SearXNG with JSON output (see
                          deploy/searxng/README.md), at --search-url
Without either, ATLAS answers with its rules, as it always has, and says
plainly when it did not search the web. --check prints which.
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_MODEL = "qwen3.5:4b"


def _get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def check_model(url, model):
    """(ok, detail) — Ollama answering, and the model pulled."""
    try:
        names = [m.get("name") for m in _get(url.rstrip("/") + "/api/tags").get("models") or []]
    except Exception as error:
        return False, "Ollama is not answering at {0} ({1}). Start it, or install it " \
                      "from ollama.com.".format(url, type(error).__name__)
    wanted = model if ":" in model else model + ":latest"
    if wanted not in names:
        return False, "Ollama is running but {0} is not pulled. Run: ollama pull {0}".format(model)
    return True, "{0} ready at {1}".format(model, url)


def check_search(url):
    """(ok, detail) — a real query through the search service."""
    if not url:
        return False, "no search service configured (--search-url)"
    try:
        found = _get(url.rstrip("/") + "/search?format=json&q=air+waybill", timeout=10)
        count = len(found.get("results") or [])
    except Exception as error:
        return False, "the search service at {0} did not answer ({1})".format(
            url, type(error).__name__)
    if not count:
        return False, "the search service at {0} answered with no results " \
                      "(its engines may be blocked from this network)".format(url)
    return True, "{0} results for a test query at {1}".format(count, url)


def seed_test_run(bridge):
    """A small run, labelled TEST DATA everywhere ATLAS answers from it."""
    bridge.run_started(run_id="TEST-DATA-DEMO", dry_run=True, target_status="TEST DATA (demo)",
                       max_records=4, max_pages=1)
    bridge.page_scanned(1, 4)
    rows = [
        (dict(bol_awb="1570046231", carrier="QATAR AIRWAYS", provider="QATAR",
              current_eta="18/08/2026", table_page=1),
         dict(provider="Qatar Airways", tracking_status="Arrived",
              eta="24/08/2026", ata="20/08/2026"), "SUCCESS", "", None),
        (dict(bol_awb="5271993480", carrier="DHL EXPRESS", provider="DHL",
              current_eta="", table_page=1),
         dict(provider="DHL", tracking_status="Estimated Delivery only",
              eta="26/08/2026", ata=None), "SUCCESS", "", None),
        (dict(bol_awb="1570049117", carrier="QATAR AIRWAYS", provider="QATAR",
              current_eta="15/08/2026", table_page=1),
         {}, "SKIPPED", "The carrier did not provide ETA or ATA.", "NO RESULT"),
        (dict(bol_awb="8842001173", carrier="DHL GLOBAL FORWARDING", provider="DHL",
              current_eta="12/08/2026", table_page=1),
         {}, "FAILED", "Save/Update button was not found on the Manage page.",
         "UNEXPECTED PAGE STATE"),
    ]
    s = f = k = 0
    for ship, result, outcome, detail, outcome_class in rows:
        bridge.shipment_started(ship)
        if result:
            bridge.provider_result(result)
            if result.get("eta"):
                bridge.view_updated("COE", "ETA", result["eta"])
            if result.get("ata"):
                bridge.view_updated("BU", "ATA", result["ata"])
        bridge.shipment_finished(ship["bol_awb"], outcome, detail, outcome_class=outcome_class)
        s += outcome == "SUCCESS"
        k += outcome == "SKIPPED"
        f += outcome == "FAILED"
        bridge.counters(s, f, k)
    bridge.system_error("DHL", "Save/Update button was not found on the Manage page.")
    bridge.run_finished("finished")


DEMO_CAPTURES = [
    ("1570046231", "QATAR AIRWAYS", "QATAR", "provider_result", """
      <h1>Qatar Airways Cargo &middot; Track shipment</h1>
      <p class="sub">Air waybill <b>1570046231</b></p>
      <table><tr><th>Status</th><td>Arrived</td></tr>
      <tr><th>Estimated arrival (ETA)</th><td>24/08/2026</td></tr>
      <tr><th>Actual arrival (ATA)</th><td>20/08/2026</td></tr></table>"""),
    ("8842001173", "DHL GLOBAL FORWARDING", "DHL", "failed", """
      <h1>Mantrac Logistics Hub &middot; Manage shipment</h1>
      <p class="sub">Reference <b>8842001173</b> &middot; DHL GLOBAL FORWARDING</p>
      <table><tr><th>COE ETA</th><td>12/08/2026</td></tr>
      <tr><th>Status</th><td>Under Clearance</td></tr></table>
      <p class="err">The Save/Update button is not on this page.</p>"""),
]
CAPTURE_PAGE = """<html><body style="font:15px Segoe UI,Arial,sans-serif;margin:0;background:#f4f6f8">
<div style="background:#B45309;color:#fff;padding:8px 16px;font-weight:700">TEST DATA &mdash;
demonstration capture for atlas_demo.py --test-data, not a real page</div>
<div style="padding:22px 26px">{body}</div>
<style>h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#555;margin:0 0 14px}}
table{{border-collapse:collapse;background:#fff}} th,td{{border:1px solid #ccd;padding:7px 12px;text-align:left}}
th{{background:#eef1f4}} .err{{color:#b91c1c;font-weight:600;margin-top:14px}}</style></body></html>"""


def seed_test_captures(folder):
    """Screenshots for the labelled test run, so "show me the screenshot of
    8842001173" has something real to show. Each image says TEST DATA on it.
    Uses the automation's own browser library; skipped if it cannot start."""
    from intelligence import evidence
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    made = 0
    try:
        with sync_playwright() as pw:
            options = {"headless": True}
            if os.name == "nt":
                options["channel"] = "msedge"
            elif os.path.exists("/opt/pw-browsers/chromium"):
                options["executable_path"] = "/opt/pw-browsers/chromium"
            browser = pw.chromium.launch(**options)
            page = browser.new_page(viewport={"width": 900, "height": 420})
            for ref, carrier, provider, event, body in DEMO_CAPTURES:
                page.set_content(CAPTURE_PAGE.format(body=body))
                image = folder / "{0}_{1}.png".format(ref, event)
                page.screenshot(path=str(image))
                text = folder / "{0}_{1}.txt".format(ref, event)
                text.write_text(page.inner_text("body"), encoding="utf-8")
                if evidence.register_capture(image, run_id="TEST-DATA-DEMO", reference=ref,
                                             carrier=carrier, provider=provider, event=event,
                                             text_path=text):
                    made += 1
            browser.close()
    except Exception as error:
        print("  (test screenshots skipped: {0})".format(str(error).splitlines()[0][:100]))
    return made


def main():
    parser = argparse.ArgumentParser(description="ATLAS demonstration launcher")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--replay", action="store_true",
                        help="load the last real run from --base (tracking_results.csv, logs)")
    source.add_argument("--test-data", action="store_true",
                        help="load a small run labelled TEST DATA in every answer")
    parser.add_argument("--check", action="store_true", help="report readiness and exit")
    parser.add_argument("--base", default=r"C:\Automation")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--model", default=os.environ.get("ATLAS_LLM_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=os.environ.get("ATLAS_LLM_URL")
                        or "http://127.0.0.1:11434")
    parser.add_argument("--search-url", default=os.environ.get("ATLAS_SEARCH_URL")
                        or "http://127.0.0.1:8888")
    parser.add_argument("--timeout", type=int, default=int(
        os.environ.get("ATLAS_LLM_TIMEOUT_S") or 120),
        help="seconds per model call (CPU-only machines need 60-120)")
    args = parser.parse_args()

    model_ok, model_detail = check_model(args.ollama_url, args.model)
    search_ok, search_detail = check_search(args.search_url)
    print("ATLAS demonstration — readiness")
    print("  conversation  {0}  {1}".format("READY" if model_ok else "OFF  ", model_detail))
    print("  web research  {0}  {1}".format("READY" if search_ok else "OFF  ", search_detail))
    if not model_ok:
        print("  -> ATLAS will answer with its rules only (English phrasing, no Arabic replies).")
    if not search_ok:
        print("  -> web questions will say 'I did not search the web' and why.")
    if args.check:
        return 0 if model_ok and search_ok else 1

    # Settings for this process only; nothing is written to the machine.
    if model_ok:
        os.environ["ATLAS_LLM_PROVIDER"] = "ollama"
        os.environ["ATLAS_LLM_MODEL"] = args.model
        os.environ["ATLAS_LLM_URL"] = args.ollama_url
        os.environ["ATLAS_LLM_TIMEOUT_S"] = str(args.timeout)
        # A slow answer falls back to the rules once, rather than waiting twice.
        os.environ.setdefault("ATLAS_LLM_RETRIES", "0")
        # Leave one core for the dashboard's browser (see ATLAS_LLM_THREADS).
        os.environ.setdefault("ATLAS_LLM_THREADS", str(max(1, (os.cpu_count() or 2) - 1)))
        # The same bounded queue as the tower (intelligence/autoconfig.py).
        os.environ.setdefault("ATLAS_LLM_SLOTS", "1")
        os.environ.setdefault("ATLAS_LLM_QUEUE", "2")
        os.environ.setdefault("ATLAS_LLM_WAIT_S", "150")
    if search_ok:
        os.environ["ATLAS_SEARCH_URL"] = args.search_url
    else:
        os.environ.pop("ATLAS_SEARCH_URL", None)

    from dashboard import server
    from dashboard.bridge import bridge
    if args.test_data:
        # The test run never reaches ATLAS's real learning store.
        os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="atlas_demo_")
        os.environ["ATLAS_DEMO_TEST_DATA"] = "1"
        seed_test_run(bridge)
        print("  data          TEST DATA — every answer is labelled as such")
        shots = seed_test_captures(Path(os.environ["ATLAS_INTEL_DIR"]) / "demo_captures")
        print("  screenshots   {0} TEST DATA capture(s) for \"show me the screenshot\"".format(shots))
    elif args.replay:
        server.replay(args.base)
        if (Path(args.base) / "tracking_results.csv").exists():
            print("  data          last real run from {0}".format(args.base))
        else:
            print("  data          NONE — no tracking_results.csv in {0}; ATLAS will say "
                  "there is no run data (use --test-data for a labelled test run)".format(args.base))
    else:
        print("  data          whatever this dashboard holds (no run loaded)")
    print()
    server.serve_forever(port=args.port, host="127.0.0.1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
