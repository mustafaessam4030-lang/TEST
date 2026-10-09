"""
One racer for test_po_engine.py's concurrency tests: a separate OS process.

    python fixtures/po_race_child.py process <data_dir> <reference> <pdf> <start_at>
    python fixtures/po_race_child.py send    <data_dir> <po_id>     -     <start_at>

Every racer waits until <start_at> (a shared epoch time) so they really
collide, then prints its outcome on the last line.
"""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "fixtures"))

from po import doctypes, mail as M, pipeline as P, store as S  # noqa: E402
import po_engine as F  # noqa: E402

kind, folder, target, pdf, start_at = sys.argv[1:6]
CONFIG = {"recipient": "accounts.ghana@mantrac.test", "sender": "ata@mantrac.test",
          "auto_send": False, "defaults": {"supplier": "CAT", "branch": None, "charge_to": None,
                                           "priority": None}, "confirm_wait_s": 0}
os.environ["PO_ALLOW_TEST_SEND"] = "1"
store = S.Store(folder=folder)
while time.time() < float(start_at):
    time.sleep(0.005)
if kind == "process":
    record = store.create(doctypes.DEFAULT, target, {})
    record = P.process(store, record, F.Source(Path(pdf).read_bytes(), ref=target), CONFIG,
                       sleep=lambda s: None)
    print(record["state"])
else:
    outcome = "GAVE_UP"
    for _ in range(50):
        try:
            _r, outcome = P.send(store, store.get(target), M.GraphMailer(), by="racer",
                                 confirm_wait_s=0, sleep=lambda s: None)
            break
        except S.ConcurrentUpdate:
            time.sleep(0.02)
    print(outcome)
