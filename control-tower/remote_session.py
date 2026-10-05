"""
Remote Human Action session — the run's own Edge tab, shown in an operator's
browser, while the run waits for that operator.

The automation keeps the browser. Nothing here moves Playwright anywhere:

    run (this process)                      worker agent        control plane
    ┌──────────────────────────────┐
    │ wait_for_human() loop        │
    │   Pump.run_for(2000 ms)      │  frames  ┌──────────┐ HTTPS ┌──────────┐
    │     CDP Page.startScreencast ├─────────►│  relay   ├──────►│  relay   ├─► the
    │     CDP Input.dispatch*      │◄─────────┤ (loopback│◄──────┤ (claims, │◄── holder's
    │                              │  input   │  only)   │       │  memory) │    browser
    └──────────────────────────────┘          └──────────┘       └──────────┘

* The view exists only while the run is inside wait_for_human() for THIS
  action and the control plane has attached it. Outside that loop no input
  can reach any page: the pump that applies it is not running.
* Frames and keystrokes are held in memory, never written, never logged,
  never handed to ATLAS or to the evidence store. Keystrokes are applied as
  the person typed them; this module does not read, keep or interpret them.
* The local endpoint listens on 127.0.0.1 only and answers only the token the
  worker agent gave this run in its environment.
* CDP is Chromium's DevTools protocol, which Edge speaks. If the session
  cannot be opened (another browser, or CDP refused), the view says so and the
  person completes the step at the worker as before.
"""

import base64
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_INPUT = 400
FRAME_FORMAT = {"format": "jpeg", "quality": 60, "maxWidth": 1366, "maxHeight": 900,
                "everyNthFrame": 1}
SLICE_MS = 35

VK = {"Backspace": 8, "Tab": 9, "Enter": 13, "Shift": 16, "Control": 17, "Alt": 18,
      "Escape": 27, " ": 32, "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36,
      "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
      "Delete": 46, "Meta": 91}
MOUSE = ("mousePressed", "mouseReleased", "mouseMoved", "mouseWheel")


class Broker(object):
    """Shared between the run's thread and the loopback endpoint. Thread-safe."""

    def __init__(self):
        self._cond = threading.Condition()
        self.attached = None          # action_id the control plane asked to show
        self.viewer = None
        self._frame = None            # {"seq", "data", "meta"}
        self._seq = 0
        self._input = []
        self.status = {"attached": False, "streaming": False, "reason": None}

    def attach(self, action_id, viewer=None):
        with self._cond:
            if self.attached != action_id:
                self._frame, self._input = None, []
            self.attached, self.viewer = action_id, viewer
            self.status = {"attached": True, "streaming": False,
                           "reason": "waiting for the run to reach the verification step"}
            self._cond.notify_all()

    def detach(self, action_id=None):
        with self._cond:
            if action_id is None or action_id == self.attached:
                self.attached = self.viewer = None
                self._frame, self._input = None, []
                self.status = {"attached": False, "streaming": False, "reason": "closed"}
                self._cond.notify_all()

    def is_attached(self, action_id):
        return bool(action_id) and self.attached == action_id

    def put_frame(self, data, meta):
        with self._cond:
            self._seq += 1
            self._frame = {"seq": self._seq, "data": data, "meta": meta}
            self._cond.notify_all()

    def frame(self, after=0, wait_s=8.0):
        deadline = time.time() + max(0.0, min(float(wait_s), 20.0))
        with self._cond:
            while True:
                if self._frame and self._frame["seq"] > after:
                    return self._frame
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def push_input(self, action_id, events):
        with self._cond:
            if action_id != self.attached:
                return False
            self._input.extend(events)
            del self._input[:-MAX_INPUT]
            return True

    def take_input(self):
        with self._cond:
            taken, self._input = self._input, []
            return taken

    def set_status(self, **fields):
        with self._cond:
            self.status = dict(self.status, **fields)
            self._cond.notify_all()


BROKER = Broker()


def _key_event(e):
    """One viewer key event as Input.dispatchKeyEvent parameters."""
    key, text = e.get("key") or "", e.get("text") or ""
    params = {"modifiers": int(e.get("modifiers") or 0), "key": key,
              "code": e.get("code") or ""}
    vk = VK.get(key)
    if vk is None and len(key) == 1:
        vk = ord(key.upper()) if key.isalnum() else 0
    if vk:
        params["windowsVirtualKeyCode"] = params["nativeVirtualKeyCode"] = vk
    if e["type"] == "keyDown":
        if text and not (params["modifiers"] & (2 | 4)):    # not with Ctrl/Meta
            params.update(type="keyDown", text=text, unmodifiedText=text)
        else:
            params["type"] = "rawKeyDown"
            if key == "Enter":
                params.update(type="keyDown", text="\r", unmodifiedText="\r")
    elif e["type"] == "keyUp":
        params["type"] = "keyUp"
    else:
        params = {"type": "char", "text": text, "modifiers": params["modifiers"]}
    return params


class Pump(object):
    """
    The screencast and the input, for one page. Lives on the run's Playwright
    thread: every CDP call is made from run_for(), never from another thread.
    """

    def __init__(self, page, broker, action_id, label=None, reference=None):
        self.page, self.broker, self.action_id = page, broker, action_id
        self.label, self.reference = label, reference
        self.cdp = None
        self._acks = []
        self.failed = None

    def start(self):
        try:
            self.cdp = self.page.context.new_cdp_session(self.page)
            self.cdp.on("Page.screencastFrame", self._on_frame)
            self.cdp.send("Page.enable")
            self.cdp.send("Page.startScreencast", FRAME_FORMAT)
            # The screencast sends a frame only when the page paints; a person
            # who just arrived needs one now.
            shot = self.cdp.send("Page.captureScreenshot",
                                 {"format": "jpeg", "quality": 60})
            metrics = self.cdp.send("Page.getLayoutMetrics")
            view = metrics.get("cssLayoutViewport") or metrics.get("layoutViewport") or {}
            self.broker.put_frame(base64.b64decode(shot["data"]), {
                "width": view.get("clientWidth"), "height": view.get("clientHeight"),
                "scale": 1, "offset_top": 0})
            self.broker.set_status(attached=True, streaming=True, reason=None,
                                   carrier=self.label, reference=self.reference,
                                   page_state="verification")
            return True
        except Exception as error:
            self.failed = str(error).split("\n")[0][:160]
            self.broker.set_status(attached=True, streaming=False,
                                   reason="The browser view could not be opened on the "
                                          "worker: {0}. Complete the step at the worker."
                                   .format(self.failed))
            self.stop()
            return False

    def _on_frame(self, params):
        # Called while run_for() is inside a Playwright call. Only data is
        # moved here; the acknowledgement is sent from run_for() itself.
        try:
            meta = params.get("metadata") or {}
            self.broker.put_frame(base64.b64decode(params["data"]), {
                "width": meta.get("deviceWidth"), "height": meta.get("deviceHeight"),
                "scale": meta.get("pageScaleFactor"), "offset_top": meta.get("offsetTop")})
            self._acks.append(params.get("sessionId"))
        except Exception:
            pass

    def _apply(self, events):
        for e in events:
            kind = e.get("type")
            if kind in MOUSE:
                params = {"type": kind, "x": float(e.get("x") or 0),
                          "y": float(e.get("y") or 0),
                          "modifiers": int(e.get("modifiers") or 0)}
                if kind == "mouseWheel":
                    params.update(deltaX=float(e.get("deltaX") or 0),
                                  deltaY=float(e.get("deltaY") or 0))
                else:
                    params.update(button=e.get("button") or "none",
                                  clickCount=int(e.get("clickCount") or 0))
                self.cdp.send("Input.dispatchMouseEvent", params)
            elif kind in ("keyDown", "keyUp", "char"):
                self.cdp.send("Input.dispatchKeyEvent", _key_event(e))

    def run_for(self, ms):
        """Pump frames and input for `ms`, on this thread. Raises like page.wait_for_timeout."""
        end = time.time() + ms / 1000.0
        while True:
            if self.cdp is None or not self.broker.is_attached(self.action_id):
                remaining = int((end - time.time()) * 1000)
                if remaining > 0:
                    self.page.wait_for_timeout(remaining)
                return
            try:
                while self._acks:
                    self.cdp.send("Page.screencastFrameAck", {"sessionId": self._acks.pop(0)})
                events = self.broker.take_input()
                if events:
                    self._apply(events)
            except Exception as error:
                if self.page.is_closed():
                    raise
                self.failed = str(error).split("\n")[0][:160]
            remaining = int((end - time.time()) * 1000)
            if remaining <= 0:
                return
            self.page.wait_for_timeout(min(SLICE_MS, remaining))

    def stop(self):
        if self.cdp is not None:
            try:
                self.cdp.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                self.cdp.detach()
            except Exception:
                pass
        self.cdp = None


class Sessions(object):
    """What wait_for_human() talks to. One pump at most, for the waiting action."""

    def __init__(self, broker=BROKER):
        self.broker = broker
        self.pump = None

    @property
    def enabled(self):
        return _SERVER["port"] is not None

    def pause(self, page, action, ms):
        """Wait `ms` on the waiting tab — streaming it if a person is attached."""
        ids = {action.get("action_id"), action.get("queue_id")} - {None}
        attached = next((i for i in ids if self.broker.is_attached(i)), None)
        if attached is None:
            if self.pump is not None:
                self.end()
            page.wait_for_timeout(ms)
            return
        if self.pump is None or self.pump.page is not page or \
                self.pump.action_id != attached:
            self.end()
            self.pump = Pump(page, self.broker, attached, action.get("carrier"),
                             action.get("reference"))
            if not self.pump.start():
                failed = self.pump
                self.pump = None
                page.wait_for_timeout(ms)
                self.broker.set_status(reason=failed.failed and
                                       "The browser view could not be opened on the worker.")
                return
        self.pump.run_for(ms)

    def note(self, action, page_state, reason=None):
        ids = {action.get("action_id"), action.get("queue_id")} - {None}
        if any(self.broker.is_attached(i) for i in ids):
            self.broker.set_status(page_state=page_state, reason=reason)

    def end(self, action=None, outcome=None):
        if self.pump is not None:
            self.pump.stop()
            self.pump = None
        if action is not None and outcome is not None:
            self.note(action, "closed", outcome)


SESSIONS = Sessions()
_SERVER = {"port": None, "httpd": None}


# ── the loopback endpoint, for the worker agent ──────────────────────────

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    token = None

    def log_message(self, *args):
        pass                       # nothing about a session is ever logged

    def _reply(self, status, body=b"", kind="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _ok(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        return hmac.compare_digest(str(self.headers.get("X-Session-Token") or ""),
                                   str(self.token or "-"))

    def _body(self, limit=64 * 1024):
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > limit:
            raise ValueError("too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if not self._ok():
            self._reply(401, {"error": "unauthorized"})
            return
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/session/status":
            self._reply(200, dict(BROKER.status, action_id=BROKER.attached))
            return
        if path == "/session/frame":
            if params.get("action_id") != BROKER.attached:
                self._reply(409, {"error": "not_attached"})
                return
            frame = BROKER.frame(int(params.get("after") or 0),
                                 float(params.get("wait") or 8))
            if frame is None:
                self._reply(204, b"", "text/plain")
                return
            self._reply(200, frame["data"], "image/jpeg", {
                "X-Seq": str(frame["seq"]), "X-Meta": json.dumps(frame["meta"])})
            return
        self._reply(404, {"error": "not_found"})

    def do_POST(self):
        if not self._ok():
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            body = self._body()
        except Exception:
            self._reply(400, {"error": "bad_request"})
            return
        if self.path == "/session/attach":
            BROKER.attach(str(body.get("action_id") or "")[:64] or None,
                          str(body.get("viewer") or "")[:64] or None)
            self._reply(200, {"ok": True})
        elif self.path == "/session/detach":
            BROKER.detach(str(body.get("action_id") or "")[:64] or None)
            self._reply(200, {"ok": True})
        elif self.path == "/session/input":
            events = body.get("events")
            ok = isinstance(events, list) and BROKER.push_input(
                str(body.get("action_id") or "")[:64], events[:200])
            self._reply(200 if ok else 409, {"ok": bool(ok)})
        else:
            self._reply(404, {"error": "not_found"})


def start_local_server(port=None, token=None):
    """
    Start the loopback endpoint when the worker agent asked for it
    (CT_SESSION_PORT / CT_SESSION_TOKEN). Returns the port, or None.
    """
    port = port if port is not None else os.environ.get("CT_SESSION_PORT")
    token = token if token is not None else os.environ.get("CT_SESSION_TOKEN")
    if not port or not token or len(token) < 24:
        return None
    handler = type("SessionHandler", (_Handler,), {"token": token})
    httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="remote-session").start()
    _SERVER.update(port=httpd.server_address[1], httpd=httpd)
    return _SERVER["port"]


def stop_local_server():
    if _SERVER["httpd"] is not None:
        _SERVER["httpd"].shutdown()
        _SERVER["httpd"].server_close()
    _SERVER.update(port=None, httpd=None)
