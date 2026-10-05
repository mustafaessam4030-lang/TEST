"""
TEST FIXTURE — never used in production. The platform tests start this in
place of update_eta.py's main(), through the real worker agent and the real
Supervisor, because main() needs the live Hub and a sign-in.

Everything that matters to the remote Human Action is the real code:

  * update_eta's own Grimaldi lookup, wait_for_human(), the Human Action
    Queue, human_queue_service(), the post-verification double read, the
    validation and update_internal_shipment() with its read-back refusal;
  * the run publishes its state to CT_STATE_FILE and takes requests from
    CT_CONTROL_FILE exactly as under the supervisor;
  * remote_session's loopback endpoint and CDP pump, on a real Chromium tab.

Stand-ins, as in test_human_loop.py: the carrier site (a GNET-like page
served by the test, whose security code is drawn as an image), and the Hub's
Manage page (an in-memory Hub; the write and the read-back are the real
functions' contract). The "person" is the test, typing into the streamed
view — never this process.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HUMAN_WAIT_MS", "60000")

import update_eta as A                       # noqa: E402
import remote_session                        # noqa: E402

WORK = Path(os.environ["E2E_WORK"])
BASE = os.environ["E2E_CARRIER_BASE"]
WORK.mkdir(parents=True, exist_ok=True)
LOG = open(WORK / "runner.log", "a", encoding="utf-8")

A.HUMAN_ACTION_FILE = WORK / "human_action.json"
A.HUMAN_EVENTS_FILE = WORK / "human_actions.jsonl"
A.HUMAN_QUEUE.path = WORK / "human_queue.json"
A.write_log = lambda message, *a, **k: (LOG.write(str(message) + "\n"), LOG.flush())
A.save_page_text = lambda *a, **k: None
A.take_screenshot = lambda *a, **k: None
A.ml_record = lambda *a, **k: None
A.ml_episode_begin = lambda *a, **k: None
A.ml_episode_end = lambda *a, **k: None
A.PAGE_SETTLE_MAX_SECONDS = 1
A.CAPTCHA_POLL_MS = 300
A.HUMAN_CONFIRM_MS = 400
A.HUMAN_QUEUE_ON = True
A.HUMAN_QUEUE_GRACE_MS = int(os.environ.get("E2E_GRACE_MS", "2500"))
A.HUMAN_AUTO_RESUME = True
A.OCEAN_WRITE = True
A.VERIFY_AFTER_SAVE = True
A.PORTALS["GRIMALDI"] = dict(A.PORTALS["GRIMALDI"], urls=[BASE + "/gnet"], wait=6)

HUB, PENDING = {}, {}
A.click_manage_in_view = lambda page, view, bol, table_page: 1
A.select_shipment_info_tab = lambda page, view, field: True
A.fill_date_field = lambda page, field, value: PENDING.__setitem__(field, value)
A.save_manage_page = lambda page: HUB.update(PENDING)
A.ensure_filtered_page = lambda *a, **k: None
A.verify_saved_date = lambda page, shipment, view, field, expected: (
    HUB.get(field) == expected, "the Hub holds {0}".format(HUB.get(field)))


def publish():
    path = Path(os.environ["CT_STATE_FILE"])
    while True:
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(A.tower.snapshot(trim=True)), encoding="utf-8")
            os.replace(str(tmp), str(path))
        except Exception:
            pass
        time.sleep(0.3)


def process(pages, shipment, counts):
    """main()'s per-shipment sequence and outcome mapping (as test_human_loop)."""
    reference = shipment["bol_awb"]
    before = tuple(counts[k] for k in ("ok", "failed", "skipped", "partial", "human"))
    A.tower.shipment_started(shipment)
    result = None
    try:
        result = A.get_provider_result(pages, shipment)
        A.tower.provider_result(result)
        actions = A.update_internal_shipment(None, shipment, result)
        counts["ok"] += 1
        A.tower.shipment_finished(reference, "SUCCESS", "", actions)
    except A.CaptchaRequired as error:
        counts["human"] += 1
        reason = getattr(error, "reason", "")
        if reason == "queued":
            A.tower.shipment_finished(reference, "HUMAN_QUEUED", str(error),
                                      outcome=A.HUMAN_QUEUED)
        else:
            lost = reason == "session_lost"
            A.tower.shipment_finished(
                reference, "FAILED" if lost else "HUMAN_TIMEOUT", str(error),
                outcome=A.HUMAN_SESSION_LOST if lost else A.HUMAN_TIMEOUT)
    except A.SkipShipment as error:
        counts["skipped"] += 1
        A.tower.shipment_finished(reference, "SKIPPED", str(error))
    except Exception as error:
        import traceback
        LOG.write(traceback.format_exc())
        LOG.flush()
        counts["failed"] += 1
        A.tower.shipment_finished(reference, "FAILED", str(error))
    A.tower.counters(counts["ok"], counts["failed"], counts["skipped"],
                     counts["partial"], needs_human=counts["human"])
    after = tuple(counts[k] for k in ("ok", "failed", "skipped", "partial", "human"))
    A.human_shipment_closed(reference, before, after, looked_up=bool(result))


def main():
    from playwright.sync_api import sync_playwright
    threading.Thread(target=publish, daemon=True).start()
    A.tower_control.configure(True, human_enabled=True)
    remote_session.start_local_server()
    A.tower.run_started(run_id=A.RUN_ID, dry_run=False, target_status="Under Clearance",
                        max_records=10, max_pages=1)
    A.tower.register_system("GRIMALDI", "Grimaldi Lines")
    counts = {"ok": 0, "failed": 0, "skipped": 0, "partial": 0, "human": 0}
    references = [r for r in os.environ.get("E2E_REFS", "S330348776").split(",") if r]
    exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    executable_path=str(exe[-1]) if exe else None)
        context = browser.new_context(viewport={"width": 1100, "height": 700})
        pages = {"DHL": context.new_page()}      # the run always holds an anchor tab
        for reference in references:
            process(pages, {"bol_awb": reference, "carrier": "Grimaldi",
                            "provider": "GRIMALDI", "current_eta": "",
                            "table_page": 1}, counts)
            pages.pop("GRIMALDI", None)
        hold_until = time.time() + float(os.environ.get("E2E_HOLD_S", "60"))
        while A.HUMAN_QUEUE.open_tasks() and time.time() < hold_until:
            if A.tower_control.should_stop():
                break
            chosen = A.human_queue_service()
            if not chosen:
                time.sleep(0.4)
                continue
            for _task, shipment in chosen:
                counts["human"] = max(0, counts["human"] - 1)
                process(pages, shipment, counts)
                pages.pop("GRIMALDI", None)
        for task in A.HUMAN_QUEUE.close_open(A.hq.TIMEOUT, "the run ended"):
            A.tower.shipment_finished(task["reference"], "HUMAN_TIMEOUT",
                                      "Still in the queue when the run ended.",
                                      outcome=A.HUMAN_TIMEOUT)
        browser.close()
    A.tower.run_finished()
    time.sleep(0.8)                 # the last state reaches the state file
    LOG.write("HUB {0}\n".format(json.dumps(HUB)))
    LOG.flush()


if __name__ == "__main__":
    main()
