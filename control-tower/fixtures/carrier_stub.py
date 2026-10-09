"""
TEST FIXTURE — a GNET-like carrier page for the platform tests. Its
security code is drawn as an image, so only someone LOOKING at the page
(the person, through the streamed view) can read it. The page reports where
its boxes are so the test — playing the person — knows where to click.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

CODE = "K7Q4"
LAYOUT = {}
SUBMITTED = []


def _code_image(code):
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='120' height='40'>"
           "<rect width='120' height='40' fill='#eee'/><text x='14' y='29' "
           "font-size='24' font-family='monospace' fill='#333'>{0}</text></svg>").format(code)
    from urllib.parse import quote
    return "data:image/svg+xml," + quote(svg)


def gnet(message=""):
    return ("<!doctype html><html><body style='font:16px Arial;margin:30px'>"
            "<h2>Container Tracking</h2><p id='msg' style='color:#b00'>" + message + "</p>"
            "<form id='f' action='/gresult' method='get'><table>"
            "<tr><td>Equipment #</td><td><input name='equip' type='text'></td>"
            "<td>Shipment #</td><td><input name='ship' type='text'></td></tr>"
            "<tr><td>Security Code</td><td><img alt='' src=\"" + _code_image(CODE) + "\"></td>"
            "<td><input id='code' name='code' type='text' placeholder='Enter code'></td>"
            "<td><button id='go' type='submit'>Search</button></td></tr></table></form>"
            "<script>function where(){var o={};['code','go'].forEach(function(i){"
            "var r=document.getElementById(i).getBoundingClientRect();"
            "o[i]={x:r.x+r.width/2,y:r.y+r.height/2};});"
            "fetch('/layout',{method:'POST',body:JSON.stringify(o)});}"
            "where();setTimeout(where,500);</script>"
            "<p>" + "x" * 200 + "</p></body></html>")


def result_page(reference):
    return ("<!doctype html><html><body><h2>Tracking result</h2>"
            "<p>Shipment # " + reference + "</p>"
            "<table><tr><td>Port of Discharge ALEXANDRIA</td><td>ETA 02/10/2026</td>"
            "<td>Actual Arrival 03/10/2026</td></tr></table>"
            "<p>" + "x" * 200 + "</p></body></html>")


class Carrier(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, body, kind="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            LAYOUT.update(json.loads(self.rfile.read(length) or b"{}"))
        except ValueError:
            pass
        self._send("{}", "application/json")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/gresult"):
            q = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
            SUBMITTED.append({"ship": q.get("ship"), "code_ok": q.get("code") == CODE})
            if q.get("code") != CODE:
                self._send(gnet("The security code is not correct."))
                return
            self._send(result_page(q.get("ship") or q.get("equip") or ""))
            return
        if parsed.path.startswith("/gnet"):
            self._send(gnet())
            return
        self._send("<html><body>" + "x" * 200 + "</body></html>")


def start(port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Carrier)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:{0}".format(server.server_address[1])
