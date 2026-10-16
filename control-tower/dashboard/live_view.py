"""
The live Human Action view for the LOCAL dashboard (START_TOWER.bat): the
run's paused carrier tab, shown in the operator's own browser, with their
mouse and keyboard passed back to it — the same view the platform gives,
without the platform.

    operator's browser ──► dashboard server (this process) ──► the run
       /api/session/<id>/frame|input|release     127.0.0.1, per-run token
                                                 (remote_session.py)

* The supervisor starts every run with its own loopback port and a random
  token (configure()); nothing else can reach the run's endpoint.
* The first dashboard tab to Open a Human Action holds it (the same rule as
  Resume). Only the holder gets frames or may send input.
* Frames and keystrokes pass through memory only: never written, never
  logged, never given to ATLAS or the evidence store. Keystrokes go to the
  carrier page exactly as typed; nothing here reads them beyond their shape.
* Off with ATA_LOCAL_LIVE_VIEW=0 — Open Session then only brings the tab to
  the front of the Edge window on the automation server, as before.
"""

import json
import os
import secrets
import socket
import threading
import urllib.error
import urllib.request

INPUT_TYPES = ("mousePressed", "mouseReleased", "mouseMoved", "mouseWheel",
               "keyDown", "keyUp", "char")

_lock = threading.Lock()
_target = {"port": None, "token": None}
_holders = {}                       # action_id -> client_id


def enabled():
    return os.environ.get("ATA_LOCAL_LIVE_VIEW", "1").strip().lower() not in (
        "0", "false", "no", "off")


def new_run_env():
    """The CT_SESSION_* settings for a run the local supervisor starts: a free
    loopback port and a fresh random token. {} when the view is off."""
    if not enabled():
        return {}
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    token = secrets.token_urlsafe(32)
    configure(port, token)
    return {"CT_SESSION_PORT": str(port), "CT_SESSION_TOKEN": token}


def configure(port, token):
    with _lock:
        _target.update(port=int(port) if port else None, token=token or None)
        _holders.clear()


def available():
    return bool(enabled() and _target["port"] and _target["token"])


def _call(method, path, body=None, timeout=12.0):
    """(status, headers, bytes) from the run's loopback endpoint."""
    url = "http://127.0.0.1:{0}{1}".format(_target["port"], path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "X-Session-Token": _target["token"] or "", "Content-Type": "application/json"})
    # Loopback only: never through a system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read()


def holder(action_id):
    return _holders.get(action_id)


def open_for(action_id, client_id):
    """The operator pressed Open: attach the run's view to them.
    -> (ok, message)."""
    if not available():
        return False, "The live view is not available for this run."
    if not action_id or not client_id:
        return False, "No Human Action or dashboard tab given."
    with _lock:
        current = _holders.get(action_id)
        if current and current != client_id:
            return False, "Another dashboard tab is handling this Human Action."
        _holders[action_id] = client_id
    try:
        status, _h, _b = _call("POST", "/session/attach",
                               {"action_id": action_id, "viewer": client_id}, timeout=5)
    except OSError:
        return False, "The run is not answering — complete the step at the automation server."
    return status == 200, None if status == 200 else "The run refused the live view."


def release(action_id, client_id):
    with _lock:
        if _holders.get(action_id) not in (None, client_id):
            return False
        _holders.pop(action_id, None)
    if available():
        try:
            _call("POST", "/session/detach", {"action_id": action_id}, timeout=5)
        except OSError:
            pass
    return True


def frame(action_id, client_id, after):
    """(status, headers, body) for the viewer: 200 a JPEG frame, 204 nothing
    new yet (X-View says why), 409 not this tab's."""
    if _holders.get(action_id) != client_id:
        return 409, {}, json.dumps({"error": "not_holder", "message":
                                    "This browser view is not open for this tab. Press Open "
                                    "Session first."}).encode("utf-8")
    try:
        status, headers, body = _call("GET", "/session/frame?action_id={0}&after={1}&wait=8"
                                      .format(action_id, int(after or 0)))
    except OSError:
        return 204, {"X-View": json.dumps({"reason": "The run is not answering."})}, b""
    if status == 200:
        return 200, {"X-Seq": headers.get("X-Seq", "0"),
                     "X-Meta": headers.get("X-Meta", "{}")}, body
    view = {}
    try:
        _s, _h, raw = _call("GET", "/session/status", timeout=3)
        view = json.loads(raw or b"{}")
    except (OSError, ValueError):
        pass
    if status == 409:
        view.setdefault("reason", "Waiting for the run to reach the verification step.")
    return 204, {"X-View": json.dumps({"reason": view.get("reason")})}, b""


def clean_events(events):
    """Only well-formed pointer and key events, bounded. Raises ValueError."""
    if not isinstance(events, list) or len(events) > 120:
        raise ValueError("events must be a list of at most 120")
    out = []
    for e in events:
        if not isinstance(e, dict) or e.get("type") not in INPUT_TYPES:
            raise ValueError("unknown event")
        item = {"type": e["type"], "modifiers": max(0, min(15, int(e.get("modifiers") or 0)))}
        if e["type"].startswith("mouse"):
            for k in ("x", "y"):
                v = float(e.get(k, 0))
                if not 0 <= v <= 10000:
                    raise ValueError("pointer outside the page")
                item[k] = round(v, 1)
            item["button"] = e.get("button") if e.get("button") in (
                "left", "right", "middle", "none") else "left"
            item["clickCount"] = max(0, min(3, int(e.get("clickCount") or 0)))
            if e["type"] == "mouseWheel":
                item["deltaX"] = max(-3000, min(3000, float(e.get("deltaX") or 0)))
                item["deltaY"] = max(-3000, min(3000, float(e.get("deltaY") or 0)))
        else:
            key, text = str(e.get("key") or ""), str(e.get("text") or "")
            if len(key) > 24 or len(text) > 4:
                raise ValueError("key too long")
            item.update(key=key, text=text, code=str(e.get("code") or "")[:24])
        out.append(item)
    return out


def send_input(action_id, client_id, events):
    """-> (status, payload). The events are passed on, never echoed or kept."""
    if _holders.get(action_id) != client_id:
        return 409, {"error": "not_holder"}
    try:
        events = clean_events(events)
    except (ValueError, TypeError):
        return 400, {"error": "bad_events"}
    try:
        status, _h, _b = _call("POST", "/session/input",
                               {"action_id": action_id, "events": events}, timeout=5)
    except OSError:
        return 503, {"error": "run_unavailable"}
    return (200, {"ok": True}) if status == 200 else (409, {"error": "not_attached"})
