"""TEST FIXTURE — a browser-like client for the control plane: cookies, CSRF, Origin."""

import http.cookiejar
import json
import urllib.error
import urllib.request


class Client(object):
    def __init__(self, base, origin=None):
        self.base = base
        self.origin = origin or base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    def call(self, method, path, body=None, headers=None, raw=None, timeout=20,
             csrf=True):
        h = {"Origin": self.origin}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        elif raw is not None:
            data = raw
        if csrf and self.csrf and method not in ("GET", "HEAD"):
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            response = self.opener.open(request, timeout=timeout)
            status, payload, hdrs = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            status, payload, hdrs = error.code, error.read(), dict(error.headers)
        kind = hdrs.get("Content-Type") or ""
        if kind.startswith("application/json"):
            try:
                payload = json.loads(payload or b"{}")
            except ValueError:
                pass
        return status, payload, hdrs

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body if body is not None else {}, **kw)

    def login(self, email, password):
        status, data, hdrs = self.post("/api/auth/login", {"email": email, "password": password})
        if status == 200:
            self.csrf = data["csrf"]
        return status, data, hdrs
