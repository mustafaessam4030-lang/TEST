"""
A PO worker that dies at a chosen point — for test_po_engine.py's crash tests.

    python fixtures/po_crash_child.py <data_dir> <po_id> <pdf_path> <crash>

<crash>:
    transition:N    the process is killed (os._exit) right after its N-th state
                    transition was written to disk
    after_create    killed after Graph created the draft, before its id is saved
    after_send      killed after Graph accepted the send, before that is recorded
    none            runs to the end

It processes the job and sends it (through the stand-in Graph named in the
environment) exactly as a worker would. A hard exit: no cleanup, no finally,
no flush beyond what the store itself made durable.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "fixtures"))

from po import mail as M, pipeline as P, store as S  # noqa: E402
import po_engine as F  # noqa: E402

folder, po_id, pdf_path, crash = sys.argv[1:5]
CONFIG = {"recipient": "accounts.ghana@mantrac.test", "sender": "ata@mantrac.test",
          "auto_send": False, "defaults": {"supplier": "CAT", "branch": None, "charge_to": None,
                                           "priority": None}, "confirm_wait_s": 0}
os.environ["PO_ALLOW_TEST_SEND"] = "1"

count = {"n": 0}
original = S.Store.transition


def transition(self, *a, **k):
    record = original(self, *a, **k)
    count["n"] += 1
    if crash == "transition:{0}".format(count["n"]):
        os._exit(137)
    return record


S.Store.transition = transition


class Mailer(M.GraphMailer):
    def create(self, *a, **k):
        out = M.GraphMailer.create(self, *a, **k)
        if crash == "after_create":
            os._exit(137)
        return out

    def send(self, *a, **k):
        out = M.GraphMailer.send(self, *a, **k)
        if crash == "after_send":
            os._exit(137)
        return out


store = S.Store(folder=folder)
record = store.get(po_id)
data = Path(pdf_path).read_bytes()
record = P.process(store, record, F.Source(data, ref=record["reference"]), CONFIG,
                   sleep=lambda s: None)
if record["state"] == S.EMAIL_PREPARED:
    record, outcome = P.send(store, record, Mailer(), by="worker", confirm_wait_s=0,
                             sleep=lambda s: None)
print(record["state"])
