"""
Control Tower server — standard library only, no pip install required.

Serves the dashboard and streams real automation state over Server-Sent Events.

Two ways to run it:

  1. Live (recommended). The patched update_eta.py starts this automatically.
  2. Review. `python -m dashboard.server --replay` loads the last finished run
     from tracking_results.csv and the newest log file, so you can inspect a
     completed run without launching Edge.
"""

import argparse
import csv
import io
import json
import mimetypes
import socket
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

if __package__ in (None, ""):
    _HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(_HERE.parent))
    sys.path.insert(0, str(_HERE))
    try:
        from dashboard.bridge import bridge
        from dashboard import assistant
        from dashboard.control import control
        from dashboard import feedback as feedback_store
        from dashboard import mlstatus
    except ImportError:
        from bridge import bridge
        import assistant
        from control import control
        import feedback as feedback_store
        import mlstatus
else:
    from .bridge import bridge
    from . import assistant
    from .control import control
    from . import feedback as feedback_store
    from . import mlstatus

def _find_static():
    """Locate the folder holding index.html, whatever the layout."""
    here = Path(__file__).resolve().parent
    for candidate in (
        here / "static",              # dashboard/static/  (normal)
        here / "dashboard" / "static",
        here.parent / "static",       # flattened one level up
        here,                         # everything in one folder
    ):
        if (candidate / "index.html").exists():
            return candidate
    return here / "static"            # nothing found; report 404 honestly


STATIC_DIR = _find_static()

# ============================================================
# NETWORK ACCESS
# ============================================================
#
# Default is loopback: the dashboard is reachable only from the machine
# running the automation. Set DASHBOARD_HOST = "0.0.0.0" in update_eta.py to
# let colleagues open it from their own machines.
#
# ACCESS_KEY is optional but strongly recommended once you leave loopback.
# The dashboard is read-only — it cannot start, stop or alter the automation —
# but it does show live shipment references, carriers and dates, and the
# assistant will answer questions about them. Anyone who can reach the port
# can read all of that.
ACCESS_KEY = None          # set via start(access_key=...)
COOKIE_NAME = "ct_key"
_shared_host = False       # True once bound to something other than loopback
_shared_port = 8787   # overwritten by start()


FIREWALL_RULE = "Mantrac Control Tower"


def ensure_firewall_rule(port):
    """
    Make sure Windows lets colleagues reach this port.

    Windows blocks inbound connections by default, which is the single reason a
    shared link "refuses to connect" from another machine. Adding the rule needs
    Administrator, so this is best-effort: if we have the rights we do it
    silently and the link just works; if we do not, we say exactly what to run.

    Scoped to domain and private profiles — never public networks.
    """
    if os.name != "nt":
        return "not-windows"

    import subprocess

    def run(args):
        return subprocess.run(
            args, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        existing = run(["netsh", "advfirewall", "firewall", "show", "rule",
                        "name={0}".format(FIREWALL_RULE)])
        if existing.returncode == 0 and str(port) in existing.stdout:
            return "already-allowed"

        added = run(["netsh", "advfirewall", "firewall", "add", "rule",
                     "name={0}".format(FIREWALL_RULE), "dir=in", "action=allow",
                     "protocol=TCP", "localport={0}".format(port),
                     "profile=domain,private"])
        if added.returncode == 0:
            print("  Windows Firewall: inbound TCP {0} allowed automatically."
                  .format(port), flush=True)
            return "added"
        return "needs-admin"
    except Exception:
        return "needs-admin"


def local_addresses():
    """Every address a colleague could realistically use to reach this box."""
    found = []
    try:
        hostname = socket.gethostname()
        found.append(hostname)
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except Exception:
        pass
    if len(found) < 2:
        # Fall back to asking the OS which interface reaches the outside world.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if address not in found:
                found.append(address)
        except Exception:
            pass
        finally:
            probe.close()
    return found
DEFAULT_PORT = 8787

try:
    import psutil
except ImportError:
    psutil = None


def machine_health():
    """Only reported when psutil is installed. Otherwise the panel says so."""
    if psutil is None:
        return {"available": False}
    try:
        process = psutil.Process(os.getpid())
        return {
            "available": True,
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "memory_percent": round(psutil.virtual_memory().percent, 1),
            "process_memory_mb": round(process.memory_info().rss / (1024 * 1024), 1),
            "threads": process.num_threads(),
        }
    except Exception:
        return {"available": False}


def build_payload(trim=True):
    """The browser gets the trimmed view; the assistant gets everything."""
    data = bridge.snapshot(trim=trim)
    data["control"] = control.snapshot()
    data["health"] = machine_health()
    return data


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # keep the automation console clean

    # -- helpers -----------------------------------------------------------

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._maybe_set_cookie()
        self.end_headers()
        self.wfile.write(body)

    def _maybe_set_cookie(self):
        """Remember a valid ?key= so assets and the SSE stream also pass."""
        if getattr(self, "_set_cookie", False) and ACCESS_KEY:
            self.send_header(
                "Set-Cookie",
                "{0}={1}; Path=/; SameSite=Lax; Max-Age=86400".format(
                    COOKIE_NAME, ACCESS_KEY),
            )
            self._set_cookie = False

    def _send_file(self, path):
        if not path.exists() or not path.is_file():
            self._send(404, json.dumps({"error": "not found"}))
            return
        guessed = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self._maybe_set_cookie()
        self.end_headers()
        self.wfile.write(data)

    # -- routes ------------------------------------------------------------

    def _authorised(self):
        """
        True when the request may proceed.

        Always true when no ACCESS_KEY is configured. Otherwise the key may
        arrive as ?key=... (first visit) or as a cookie (every request after).
        """
        if not ACCESS_KEY:
            return True
        from urllib.parse import urlparse, parse_qs

        supplied = (parse_qs(urlparse(self.path).query).get("key") or [None])[0]
        if supplied == ACCESS_KEY:
            self._set_cookie = True
            return True
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME and value == ACCESS_KEY:
                return True
        return False

    def _deny(self):
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<title>Control Tower</title>"
            "<style>body{background:#0A0C0E;color:#fff;font:15px/1.6 system-ui;"
            "display:grid;place-items:center;height:100vh;margin:0;text-align:center}"
            "b{color:#FF7A00}</style>"
            "<div><h2>Control Tower</h2><p>This dashboard needs an access key.</p>"
            "<p>Open the link that includes <b>?key=…</b></p></div>"
        ).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorised():
            self._deny()
            return
        route = self.path.split("?")[0].rstrip("/") or "/"

        if route == "/":
            self._send_file(STATIC_DIR / "index.html")
            return

        if route == "/api/state":
            self._send(200, json.dumps(build_payload()))
            return

        if route == "/api/stream":
            self._stream()
            return

        if route.startswith("/static/"):
            relative = route[len("/static/"):]
            candidate = (STATIC_DIR / relative).resolve()
            if STATIC_DIR.resolve() in candidate.parents or candidate == STATIC_DIR.resolve():
                self._send_file(candidate)
            else:
                self._send(403, json.dumps({"error": "forbidden"}))
            return

        if route == "/api/export.csv":
            self._export_csv()
            return

        # /intro is the honest name; /film stays so older links keep working.
        if route in ("/intro", "/film"):
            self._send_file(STATIC_DIR / "film.html")
            return

        if route == "/api/film":
            self._send(200, json.dumps(film_scenes()))
            return

        if route == "/api/share":
            addresses = [a for a in local_addresses()] if _shared_host else []
            key = "?key={0}".format(ACCESS_KEY) if ACCESS_KEY else ""
            self._send(200, json.dumps({
                "shared": bool(_shared_host),
                "port": _shared_port,
                "links": ["http://{0}:{1}/{2}".format(a, _shared_port, key)
                          for a in addresses],
            }))
            return

        if route == "/api/ml":
            # Real values from the live ml package. Nothing here is a demo
            # figure: an unknown is null, not zero.
            payload = mlstatus.snapshot()
            payload["feedback"] = feedback_store.stats()
            self._send(200, json.dumps(payload))
            return

        if route == "/api/music":
            self._send(200, json.dumps(find_music()))
            return

        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._authorised():
            self._deny()
            return
        route = self.path.split("?")[0].rstrip("/") or "/"

        if route == "/api/control":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(min(length, 2000)) or b"{}")
                action = str(payload.get("action", ""))[:20]
                reference = str(payload.get("reference", ""))[:40]
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}))
                return
            accepted, message = control.request(action, reference)
            self._send(200, json.dumps({"accepted": accepted, "message": message}))
            return

        if route == "/api/feedback":
            # Feedback is stored as material for a later, deliberate training
            # and evaluation pass. It never reaches a production model on its
            # own.
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 8000:
                    self._send(413, json.dumps({"error": "feedback too long"}))
                    return
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}))
                return
            accepted, message = feedback_store.record(
                question=payload.get("question"),
                answer=payload.get("answer"),
                verdict=payload.get("verdict"),
                correction=payload.get("correction"),
                sources=payload.get("sources"),
                confidence=payload.get("confidence"),
                intent=payload.get("intent"),
                reference=payload.get("reference"),
            )
            self._send(200 if accepted else 400,
                       json.dumps({"accepted": accepted, "message": message}))
            return

        if route != "/api/ask":
            self._send(404, json.dumps({"error": "not found"}))
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4000:                      # nothing legitimate is bigger
                self._send(413, json.dumps({"error": "question too long"}))
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = str(payload.get("question", ""))[:1000]
            # Follow-up context is owned by the caller; the assistant keeps no
            # state between requests. Only a reference is accepted.
            raw_context = payload.get("context") or {}
            context = {"reference": str(raw_context.get("reference") or "")[:64]}
        except Exception:
            self._send(400, json.dumps({"error": "bad request"}))
            return

        # The assistant only ever receives a snapshot. It has no handle on the
        # bridge, the browser or the credentials, so it cannot act on anything.
        # Untrimmed: the assistant should see the whole run, not the wire view.
        reply = assistant.answer(question, bridge.snapshot(), context)

        # The assistant may ASK for an action but can never perform one. The
        # request goes through the same control channel and the same enabled
        # check as the dashboard buttons.
        wanted = reply.pop("request", None)
        if wanted:
            accepted, message = control.request(
                wanted.get("action"), wanted.get("reference"))
            reply["answer"] = message
            reply["accepted"] = accepted

        self._send(200, json.dumps(reply))

    EXPORT_COLUMNS = [
        ("reference", "BOL_AWB"),
        ("carrier", "Carrier"),
        ("provider", "Tracking_Provider"),
        ("hub_status", "Hub_Status_Filter"),
        ("table_page", "Hub_List_Page"),
        ("internal_eta", "Hub_ETA_Before"),
        ("provider_status", "Carrier_Status"),
        ("provider_eta", "Carrier_ETA"),
        ("provider_ata", "Carrier_ATA"),
        ("coe_action", "COE_ETA_Action"),
        ("bu_action", "BU_ATA_Action"),
        ("state", "Result"),
        ("outcome", "Outcome_Class"),
        ("duration_ms", "Processing_ms"),
        ("error", "Detail"),
        ("started_at", "Started"),
        ("updated", "Last_Updated"),
    ]

    def _export_csv(self):
        """
        Export the run's shipments as CSV.

        ?state=updated  (default) only shipments written to the hub
        ?state=all|failed|skipped|processing

        Straight from the live state — no re-derivation, no invented columns.
        An empty value stays empty rather than becoming a placeholder.
        """
        from urllib.parse import urlparse, parse_qs

        wanted = (parse_qs(urlparse(self.path).query).get("state") or ["updated"])[0]
        data = bridge.snapshot()
        rows = data.get("shipments") or []
        if wanted != "all":
            rows = [r for r in rows if r.get("state") == wanted]

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([label for _key, label in self.EXPORT_COLUMNS])
        for record in reversed(rows):          # oldest first, as processed
            writer.writerow([
                "" if record.get(key) is None else record.get(key)
                for key, _label in self.EXPORT_COLUMNS
            ])

        # Excel opens UTF-8 correctly only with a BOM.
        body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "control_tower_{0}_{1}.csv".format(wanted, stamp)

        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition",
                         'attachment; filename="{0}"'.format(filename))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_version = -1
        last_push = 0.0
        try:
            while True:
                payload = build_payload()
                changed = payload["version"] != last_version
                stale = (time.time() - last_push) > 2.0
                if changed or stale:
                    last_version = payload["version"]
                    last_push = time.time()
                    chunk = "event: state\ndata: {0}\n\n".format(json.dumps(payload))
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                time.sleep(0.35)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


FILM_SLOTS = [
    "01-origin", "02-road", "03-port", "04-vessel",
    "05-arrival", "06-clearance", "07-hub", "08-close",
]
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".avif")


def film_scenes():
    """
    Report which scene photographs are actually present.

    Files are matched by the number that starts the filename, so "03 port at
    dawn.jpg" and "03-port.jpg" both land in slot three. A missing photo is
    reported as missing — the film draws its own artwork for that scene rather
    than showing a broken image.
    """
    folder = STATIC_DIR / "film"
    folder.mkdir(parents=True, exist_ok=True)

    found = {}
    try:
        for item in sorted(folder.iterdir()):
            if item.suffix.lower() not in IMAGE_TYPES:
                continue
            head = item.name.split("-")[0].split(" ")[0].split(".")[0]
            if head.isdigit():
                found.setdefault(int(head), "/static/film/" + item.name)
    except Exception:
        pass

    # An optional video takes over as the backdrop and is scrubbed by scroll.
    video = None
    for item in sorted(folder.iterdir()):
        if item.suffix.lower() in (".mp4", ".webm", ".mov"):
            video = "/static/film/" + item.name
            break

    return {
        "slots": [
            {"index": i + 1, "name": name, "image": found.get(i + 1)}
            for i, name in enumerate(FILM_SLOTS)
        ],
        "video": video,
        "folder": str(folder),
        "have": len(found),
        "total": len(FILM_SLOTS),
    }


def find_music():
    """
    Report which audio and artwork files are actually present on disk.

    Two folders are searched. `static/music/` is the player's own, and takes
    precedence. If it is empty the intro's track is used instead, so a single
    file dropped in for the startup sequence also gives the player something
    to play rather than leaving it reading "No audio file found" — which is
    exactly what it did.

    Title and artist are derived from the FILENAME. They used to be hardcoded
    to one particular track, so the player named that track whatever you
    actually put in the folder.
    """
    folder = STATIC_DIR / "music"
    folder.mkdir(parents=True, exist_ok=True)
    intro_folder = STATIC_DIR / "assets" / "audio"

    AUDIO = (".mp3", ".m4a", ".ogg", ".wav", ".flac")
    ART = (".jpg", ".jpeg", ".png", ".webp")

    audio = art = source = None
    for base, url_prefix in ((folder, "/static/music/"),
                             (intro_folder, "/static/assets/audio/")):
        if not base.is_dir():
            continue
        for item in sorted(base.iterdir()):
            suffix = item.suffix.lower()
            if audio is None and suffix in AUDIO:
                audio = url_prefix + item.name
                source = item
            if art is None and suffix in ART:
                art = url_prefix + item.name
        if audio:
            break

    # "Inner_Light.mp3" -> "Inner Light"; "01 - Artist - Title.mp3" -> both.
    title, artist = "No audio file found", ""
    if source is not None:
        stem = source.stem.replace("_", " ").strip()
        parts = [p.strip() for p in stem.split(" - ") if p.strip()]
        if len(parts) >= 2:
            artist, title = parts[-2], parts[-1]
        elif parts:
            title = parts[0]
        if title.lower() == "intro":
            title = "Startup sequence"

    return {
        "audio": audio,
        "art": art,
        "title": title,
        "artist": artist,
        "album": "",
        "folder": str(folder),
    }


# ============================================================
# REPLAY — rebuild state from a real finished run
# ============================================================

LOG_LINE = re.compile(r"^\[(?P<ts>[\d\-: ]+)\]\s(?P<msg>.*)$")


def replay(base_folder):
    base = Path(base_folder)
    results = base / "tracking_results.csv"
    logs = sorted((base / "logs").glob("run_*.log")) if (base / "logs").exists() else []

    bridge.run_started(
        dry_run=None,
        target_status="Under Clearance",
        results_file=str(results),
        log_file=str(logs[-1]) if logs else None,
    )

    if logs:
        for raw in logs[-1].read_text(encoding="utf-8", errors="replace").splitlines():
            match = LOG_LINE.match(raw.strip())
            if match:
                bridge.log(match.group("msg"))

    if not results.exists():
        bridge.log("Replay: tracking_results.csv not found in {0}".format(base))
        bridge.run_finished()
        return

    successful = failed = skipped = 0
    pages = set()

    with open(results, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            reference = (row.get("BOL_AWB") or "").strip()
            if not reference:
                continue
            page = row.get("Table_Page") or ""
            if page and page not in pages:
                pages.add(page)
            provider = (row.get("Provider") or "").strip()
            bridge.shipment_started({
                "bol_awb": reference,
                "carrier": row.get("Carrier"),
                "provider": "QATAR" if "qatar" in provider.lower() else (provider or None),
                "current_eta": row.get("Existing_ETA"),
                "table_page": page,
            })
            bridge.provider_result({
                "provider": provider,
                "tracking_status": row.get("Provider_Status"),
                "eta": row.get("Provider_ETA"),
                "ata": row.get("Provider_ATA"),
            })
            outcome = (row.get("Result") or "").strip().upper()
            bridge.shipment_finished(
                reference,
                outcome,
                row.get("Details") or "",
                {"coe": row.get("COE_ETA_Action"), "bu": row.get("BU_ATA_Action")},
            )
            if outcome == "SUCCESS":
                successful += 1
            elif outcome == "SKIPPED":
                skipped += 1
            elif outcome == "FAILED":
                failed += 1
            bridge.counters(successful, failed, skipped)

    bridge.discovered = successful + failed + skipped
    bridge.pages_scanned = len(pages)
    bridge.run_finished()


# ============================================================
# LIFECYCLE
# ============================================================

_server = None


def start(port=DEFAULT_PORT, open_browser=True, host="127.0.0.1", access_key=None):
    """
    Start the dashboard in a daemon thread. Never raises into the caller.

    host="127.0.0.1"  this machine only (default)
    host="0.0.0.0"    reachable from other machines on the network
    """
    global _server, ACCESS_KEY, _shared_host, _shared_port
    ACCESS_KEY = access_key or None
    _shared_host = host not in ("127.0.0.1", "localhost")
    _shared_port = port
    try:
        class QuietServer(ThreadingHTTPServer):
            """
            Browsers abort SSE streams on navigate/refresh/close. socketserver
            prints a full traceback for that, which on Windows appears as
            ConnectionAbortedError [WinError 10053] in the middle of the run
            log. It is normal client behaviour, not a fault, so it is
            swallowed here — real errors still surface.
            """

            daemon_threads = True

            def handle_error(self, request, client_address):
                import sys as _sys
                kind = _sys.exc_info()[0]
                if kind is not None and issubclass(
                    kind, (ConnectionResetError, ConnectionAbortedError,
                           BrokenPipeError, TimeoutError)
                ):
                    return
                ThreadingHTTPServer.handle_error(self, request, client_address)

        _server = QuietServer((host, port), Handler)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, daemon=True).start()

        def pulse():
            while True:
                time.sleep(1.0)
                bridge.heartbeat()

        threading.Thread(target=pulse, daemon=True).start()

        suffix = "?key={0}".format(ACCESS_KEY) if ACCESS_KEY else ""
        local = "http://127.0.0.1:{0}/{1}".format(port, suffix)

        if host not in ("127.0.0.1", "localhost"):
            firewall = ensure_firewall_rule(port)
            print("", flush=True)
            print("=" * 66, flush=True)
            print("  ON THIS MACHINE:", flush=True)
            print("    {0}".format(local), flush=True)
            print("", flush=True)
            print("  SEND THIS TO COLLEAGUES  (127.0.0.1 will NOT work for them —", flush=True)
            print("  on their computer it points at their own machine):", flush=True)
            for address in local_addresses():
                print("    http://{0}:{1}/{2}".format(address, port, suffix), flush=True)
            if firewall == "needs-admin":
                print("", flush=True)
                print("  If they cannot connect, Windows Firewall is blocking it.", flush=True)
                print("  Close this, then right-click START_SHARED.bat >", flush=True)
                print("  'Run as administrator' — it opens the port once and starts", flush=True)
                print("  the run for you.", flush=True)
            print("=" * 66, flush=True)
            print("", flush=True)
        else:
            print("Control Tower running at {0}".format(local), flush=True)
            if not ACCESS_KEY:
                print(
                    "  NOTE: no access key is set, so anyone who can reach this "
                    "port can read the run. Set DASHBOARD_ACCESS_KEY in "
                    "update_eta.py to require one.",
                    flush=True,
                )
            print(
                "  If a colleague cannot connect, Windows Firewall is almost "
                "certainly blocking it. Allow inbound TCP on port {0} — see the "
                "README for the one-line command.".format(port),
                flush=True,
            )

        if open_browser:
            threading.Timer(0.8, lambda: webbrowser.open(local)).start()
        return local
    except OSError as error:
        if getattr(error, "errno", None) in (98, 48, 10048):
            print(
                "Control Tower could not start: port {0} is already in use.\n"
                "  Another run is probably still open at http://127.0.0.1:{0}/\n"
                "  Close it, or set DASHBOARD_PORT to a different number in update_eta.py."
                .format(port),
                flush=True,
            )
        else:
            print("Control Tower could not start: {0}".format(error), flush=True)
        return None
    except Exception as error:
        print("Control Tower could not start: {0}".format(error), flush=True)
        return None


def serve_forever(port=DEFAULT_PORT, host="127.0.0.1", access_key=None):
    start(port=port, host=host, access_key=access_key)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nControl Tower stopped.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mantrac Shipment Control Tower")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1",
                        help="0.0.0.0 to allow other machines on the network")
    parser.add_argument("--key", default=None,
                        help="require ?key=... to view (recommended off loopback)")
    parser.add_argument("--share", action="store_true",
                        help="shorthand for --host 0.0.0.0")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Load the last finished run from tracking_results.csv and logs",
    )
    parser.add_argument("--base", default=r"C:\Automation", help="Automation base folder")
    args = parser.parse_args()

    if args.replay:
        replay(args.base)
    serve_forever(port=args.port,
                  host="0.0.0.0" if args.share else args.host,
                  access_key=args.key)
