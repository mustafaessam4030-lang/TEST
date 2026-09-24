"""Controlled test website (SYNTHETIC - not the real application).

* GET/POST /login            normal HTML login form, sets a session cookie
* GET /inspections           page whose table is rendered client-side from the API
* GET /api/inspections       JSON API used by that page (401 without session)
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

USERNAME = "tester"
PASSWORD = "correct-horse-battery"
SESSION = "fixture-session-token"
HERE = Path(__file__).parent


def records() -> list[dict]:
    out = []
    for n in range(1, 6):
        out.append({
            "inspectionNo": f"3008{n:04d}",
            "serialNo": f"SYW{n:05d}",
            "status": "Completed" if n != 4 else "In Progress",
            "completedAt": f"2026-09-0{n}T08:30:00Z",
            "results": {"critical": 0, "warning": n % 2, "passed": 70 + n},
            "attachments": [{"fileName": f"report-{n}.pdf", "downloadUrl": f"/files/report-{n}.pdf"}] if n != 3 else [],
        })
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _authed(self) -> bool:
        return f"sid={SESSION}" in (self.headers.get("Cookie") or "")

    def _send(self, status: int, body: str, content_type: str = "text/html", headers: dict | None = None):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/login":
            return self._send(200, (HERE / "login.html").read_text().replace("{{error}}", ""))
        if url.path == "/inspections":
            if not self._authed():
                return self._send(302, "", headers={"Location": "/login"})
            return self._send(200, (HERE / "inspections.html").read_text())
        if url.path == "/api/inspections":
            if not self._authed():
                return self._send(401, json.dumps({"error": "unauthorized"}), "application/json")
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            items = [r for r in records() if not q.get("sn") or r["serialNo"] == q["sn"]]
            page, size = int(q.get("page", 1)), int(q.get("size", 2))
            body = {"data": {"items": items[(page - 1) * size: page * size], "total": len(items)}}
            return self._send(200, json.dumps(body), "application/json")
        return self._send(404, "not found")

    def do_POST(self):
        if urlsplit(self.path).path != "/login":
            return self._send(404, "not found")
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        if form.get("username") == USERNAME and form.get("password") == PASSWORD:
            return self._send(303, "", headers={"Location": "/inspections",
                                                "Set-Cookie": f"sid={SESSION}; Path=/; HttpOnly"})
        page = (HERE / "login.html").read_text().replace("{{error}}", '<p role="alert">Invalid credentials</p>')
        return self._send(200, page)


class FixtureServer:
    def __enter__(self) -> str:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
