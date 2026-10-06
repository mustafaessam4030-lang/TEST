"""
What a piece of evidence about the real eHub is worth — one vocabulary for
the Windows worker, the control plane, the dashboard and the logs.

    TEST            produced by the test suite
    SIMULATED       a stand-in played the part of eHub or of a carrier
    REAL OBSERVED   the Windows worker's own browser opened the real eHub and
                    saw it: the shipment list rendered, a shipment and its
                    status were read off the page
    REAL VERIFIED   ...and a value written to that shipment in the real eHub
                    was read back from eHub and was equal
    BLOCKED         real verification was attempted and could not complete;
                    the reason says exactly where it stopped

The level is computed from the evidence by classify(). A level the sender
claims is never taken over. And the control plane never earns REAL from a
request of its own: it has no route to eHub and makes none. It classifies
only what arrives on the worker channel, authenticated with a worker token —
the Windows worker is the authority on the real eHub.
"""

import os
from urllib.parse import urlparse

LEVELS = ("TEST", "SIMULATED", "REAL OBSERVED", "REAL VERIFIED", "BLOCKED")
TEST, SIMULATED, REAL_OBSERVED, REAL_VERIFIED, BLOCKED = LEVELS
KINDS = ("ehub-connection", "eta-write")
DEFAULT_EHUB_HOST = "logisticshub.mantracgroup.com"
REQUIRED_STATUS = "Under Clearance"
# The categories a BLOCKED observation is filed under.
CATEGORIES = ("NETWORK", "AUTHENTICATION", "BROWSER", "APPLICATION", "CONFIGURATION",
              "NOT_ON_WORKER")


def ehub_host():
    """The real eHub's host name. EHUB_HOST overrides it (the control plane has
    no update_eta.py to read it from)."""
    return (os.environ.get("EHUB_HOST") or DEFAULT_EHUB_HOST).strip().lower()


def host_of(url):
    return (urlparse(str(url or "")).hostname or "").lower() or None


def _same_status(found, wanted=REQUIRED_STATUS):
    return " ".join(str(found or "").split()).casefold() == wanted.casefold()


def classify(obs, channel):
    """
    (level, reasons) for one observation.

    `channel` is where it came from, decided by the receiver and never by the
    payload: "worker" — the worker command itself, on the worker, or the
    control plane's authenticated worker endpoint; "test" — the test suite;
    anything else — e.g. "cloud" — cannot be REAL.
    """
    obs = obs if isinstance(obs, dict) else {}
    if channel == "test" or obs.get("origin") == "test":
        return TEST, ["produced by the test suite"]
    if obs.get("simulated"):
        return SIMULATED, ["a stand-in played the part of eHub or the carrier"]
    if obs.get("result") == "BLOCKED":
        return BLOCKED, [obs.get("blocked_reason") or "the worker could not complete the check"]
    if channel != "worker":
        return BLOCKED, ["not observed on the Windows worker — real eHub verification is "
                         "the worker's, and this arrived on the '{0}' channel".format(channel)]

    missing = []
    browser = obs.get("browser") or {}
    if browser.get("real") is not True or not browser.get("version"):
        missing.append("no real browser session is recorded")
    want = ehub_host()
    if (obs.get("ehub_host") or "").lower() != want:
        missing.append("the eHub host is {0!r}, not {1!r}".format(obs.get("ehub_host"), want))
    page = obs.get("page") or {}
    if (page.get("host") or "").lower() != want:
        missing.append("the page that was read was on {0!r}, not on eHub".format(page.get("host")))
    if not (obs.get("shipment_list") or {}).get("rendered"):
        missing.append("the shipment list with its BOL/AWB and Status columns was not seen")
    shipment = obs.get("shipment") or {}
    if not shipment.get("reference"):
        missing.append("no shipment was read off the list")
    elif not shipment.get("status"):
        missing.append("the status of {0} was not read".format(shipment["reference"]))
    elif not _same_status(shipment.get("status")):
        missing.append("{0} is {1!r} in eHub, not {2!r}".format(
            shipment["reference"], shipment["status"], REQUIRED_STATUS))
    if missing:
        return BLOCKED, missing

    if obs.get("kind") != "eta-write":
        return REAL_OBSERVED, ["seen in the worker's browser on {0}".format(want)]

    # The write path: carrier -> identity -> write -> read back -> equal.
    carrier = obs.get("carrier_result") or {}
    writes = obs.get("writes") or []
    backs = obs.get("read_backs") or []
    gaps = []
    if not (carrier.get("eta") or carrier.get("ata")):
        gaps.append("the carrier gave no date to write")
    if carrier and carrier.get("identity_checked") is not True:
        gaps.append("the carrier page was not confirmed to carry this shipment's reference")
    if not writes:
        gaps.append("nothing was written to eHub")
    for write in writes:
        match = [b for b in backs if b.get("field") == write.get("field")
                 and b.get("written") == write.get("value")]
        if not match:
            gaps.append("{0} {1} was written but not read back".format(
                write.get("field"), write.get("value")))
        elif any(b.get("verdict") != "MATCH" or b.get("read_back") is None for b in match):
            gaps.append("{0}: wrote {1}, eHub read back {2}".format(
                write.get("field"), write.get("value"),
                next((b.get("read_back") or b.get("detail") for b in match
                      if b.get("verdict") != "MATCH" or b.get("read_back") is None), None)))
        elif any((b.get("page_host") or want) != want for b in match):
            gaps.append("the read-back of {0} was not on eHub".format(write.get("field")))
    if obs.get("shipment_outcome") != "updated":
        gaps.append("the run did not end this shipment as SUCCESS ({0})".format(
            obs.get("shipment_outcome") or "no outcome"))
    if not gaps:
        return REAL_VERIFIED, ["written to eHub and read back equal on the worker"]
    if not writes:
        # Nothing reached eHub: the proof could not be completed.
        return BLOCKED, gaps
    return REAL_OBSERVED, ["NOT VERIFIED: " + g for g in gaps]


def line(level, obs, reasons):
    """The one log line every observation gets."""
    shipment = (obs or {}).get("shipment") or {}
    head = "REAL VERIFICATION BLOCKED" if level == BLOCKED else level
    return "[VERIFY] {0} | {1} | run {2} | {3}{4} | {5}".format(
        head, (obs or {}).get("kind"), (obs or {}).get("run_id"),
        shipment.get("reference") or "-",
        " ({0})".format(shipment["status"]) if shipment.get("status") else "",
        "; ".join(str(r) for r in reasons))
