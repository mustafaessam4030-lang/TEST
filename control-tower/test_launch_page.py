"""
The launch page (/launch, dashboard/launch.py): the countdown from a fixed,
offset-carrying moment; the time zone; the launched state; the licensed
soundtrack and its fallbacks; and that none of it opens anything sign-in
protects, before or after the launch moment, in either access mode. Then the
page in real Chromium: desktop, tablet, phone, reduced motion.

    python test_launch_page.py               (LAUNCH_SHOTS=<dir> also saves screenshots)
"""
import http.cookiejar
import json
import math
import os
import socket
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="launch_page_")
os.environ["DASHBOARD_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["DASHBOARD_ACCOUNTS"] = "auto"
os.environ["ATLAS_INTEL_DIR"] = os.path.join(TMP, "intel")
os.environ["PO_DATA_DIR"] = os.path.join(TMP, "po")
os.environ["PO_AUTO"] = "0"
os.environ["DASHBOARD_ACCESS_KEY_FILE"] = os.path.join(TMP, "access_key")
for k in [k for k in os.environ if k.startswith("ATA_LAUNCH_")]:
    del os.environ[k]

from dashboard import launch                                 # noqa: E402

PASS, FAIL = [], []


def until(pg, expression, timeout=6000):
    """Poll from here: the page's CSP rightly refuses Playwright's string-eval waits."""
    end = time.time() + timeout / 1000.0
    while time.time() < end:
        if pg.evaluate(expression):
            return True
        time.sleep(0.1)
    raise AssertionError("timed out waiting for: " + expression)


SHOTS = os.environ.get("LAUNCH_SHOTS")


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name,
                                  "" if ok else "  ({0})".format(str(detail)[:300])))


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


UTC = timezone.utc
NOW = datetime(2026, 10, 22, 7, 0, 0, tzinfo=UTC)

rule("Countdown: a fixed moment, its offset, its zone")
m, why = launch.parse_moment("2026-11-01T09:00:00+02:00")
check("a moment with an offset is read exactly", m == datetime(2026, 11, 1, 7, 0, tzinfo=UTC) and why is None, m)
m, _ = launch.parse_moment("2026-11-01T07:00:00Z")
check("Z means UTC", m == datetime(2026, 11, 1, 7, 0, tzinfo=UTC))
m, why = launch.parse_moment("2026-11-01T09:00:00")
check("no offset: refused, the moment would be ambiguous", m is None and "offset" in why)
m, why = launch.parse_moment("next friday")
check("not a date: refused", m is None and "ISO" in why)

c = launch.config({"ATA_LAUNCH_AT": "2026-11-01T09:00:00+02:00", "ATA_LAUNCH_TZ": "Africa/Cairo"}, NOW)
check("countdown state before the moment", c["state"] == "countdown" and c["launch_at"] == "2026-11-01T07:00:00Z", c)
check("10 days 0 h 0 m 0 s left, computed from the moment",
      c["remaining"] == {"total_seconds": 864000, "days": 10, "hours": 0, "minutes": 0, "seconds": 0}, c["remaining"])
r = launch.remaining(datetime(2026, 10, 23, 9, 3, 4, tzinfo=UTC), NOW)
check("1 d 2 h 3 m 4 s", (r["days"], r["hours"], r["minutes"], r["seconds"]) == (1, 2, 3, 4), r)
r = launch.remaining(NOW + timedelta(milliseconds=999), NOW)
check("in the last second it still says 1 s: never 0 before the moment", r["total_seconds"] == 1)
check("never negative after it", launch.remaining(NOW - timedelta(days=3), NOW)["total_seconds"] == 0)
check("the same moment from another offset counts the same",
      launch.config({"ATA_LAUNCH_AT": "2026-11-01T07:00:00+00:00"}, NOW)["remaining"] == c["remaining"])
check("the zone defaults to UTC", launch.config({"ATA_LAUNCH_AT": "2026-11-01T07:00:00Z"}, NOW)["timezone"] == "UTC")
c = launch.config({"ATA_LAUNCH_AT": "2026-11-01T09:00:00+02:00"}, datetime(2026, 11, 1, 7, 0, tzinfo=UTC))
check("at the exact moment: launched", c["state"] == "launched" and c["remaining"]["total_seconds"] == 0, c)
c = launch.config({"ATA_LAUNCH_AT": "2026-11-01T09:00:00+02:00"}, datetime(2027, 1, 1, tzinfo=UTC))
check("long after: launched, all zeros", c["state"] == "launched" and c["remaining"]["days"] == 0)
c = launch.config({"ATA_LAUNCH_AT": "2026-11-01T09:00:00+05:00", "ATA_LAUNCH_TZ": "Africa/Cairo"}, NOW)
check("offset that does not match the zone: refused, not guessed",
      c["state"] == "invalid" and "UTC+02:00" in c["problem"] and c["launch_ms"] is None, c)
c = launch.config({"ATA_LAUNCH_AT": "2026-07-01T09:00:00+01:00", "ATA_LAUNCH_TZ": "Europe/London"}, NOW)
check("summer time is taken into account (London is UTC+01:00 in July)", c["state"] == "launched" and not c["problem"], c)
c = launch.config({"ATA_LAUNCH_AT": "2026-12-01T09:00:00+01:00", "ATA_LAUNCH_TZ": "Europe/London"}, NOW)
check("...and not in December", c["state"] == "invalid", c)
c = launch.config({"ATA_LAUNCH_AT": "2026-11-01T09:00:00+02:00", "ATA_LAUNCH_TZ": "Cairo<script>"}, NOW)
c = launch.config({"ATA_LAUNCH_AT": "2026-11-01T09:00:00+02:00", "ATA_LAUNCH_TZ": "Africa/Ciaro"}, NOW)
check("a misspelt zone: refused, not ignored", c["state"] == "invalid" and "known" in c["problem"], c)
c = launch.config({"ATA_LAUNCH_AT": "2026-11-01T09:00:00+02:00", "ATA_LAUNCH_TZ": "Cairo<script>"}, NOW)
check("a zone that is not an IANA name: refused", c["state"] == "invalid" and "IANA" in c["problem"])
c = launch.config({"ATA_LAUNCH_AT": "garbage"}, NOW)
check("a bad date: invalid, and the page is told why without the value",
      c["state"] == "invalid" and "garbage" not in json.dumps(c), c)
c = launch.config({}, NOW)
check("not configured: no countdown", c["state"] == "unset" and c["remaining"] is None)
check("the config sent to the page carries only its own fields",
      set(c) == {"state", "problem", "launch_at", "launch_ms", "timezone", "server_now_ms",
                 "remaining", "audio", "login"} and c["login"] == "/login")

rule("Soundtrack: only a licensed source that is really there")
launch.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
TONE = launch.AUDIO_DIR / "ct-test-tone.wav"
BROKEN = launch.AUDIO_DIR / "ct-test-broken.mp3"
with wave.open(str(TONE), "wb") as w:            # a generated tone, not a recording
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / 8000)))
                           for i in range(8000 * 6)))
BROKEN.write_bytes(b"this is not audio" * 40)
try:
    url, why = launch.audio_source({})
    check("nothing configured: unavailable", url is None and "no licensed" in why)
    for bad in ("../server.py", "..\\x.mp3", "sub/a.mp3", "evil.exe", "javascript:alert(1)", "http://x.test/a.mp3",
                "https://user:secret@media.example.com/a.mp3", "https://", "\\\\host\\share\\a.mp3"):
        url, why = launch.audio_source({"ATA_LAUNCH_AUDIO": bad})
        check("refused as a source: {0}".format(bad), url is None, url)
    url, why = launch.audio_source({"ATA_LAUNCH_AUDIO": "missing.mp3"})
    check("a file that is not on the server: unavailable", url is None and "not on this server" in why)
    url, why = launch.audio_source({"ATA_LAUNCH_AUDIO": "ct-test-tone.wav"})
    check("a file that is there: served from /static/launch/audio/", url == "/static/launch/audio/ct-test-tone.wav", url)
    url, _ = launch.audio_source({"ATA_LAUNCH_AUDIO": "https://media.example.com/licensed/track.mp3"})
    check("a licensed https stream is used as given", url == "https://media.example.com/licensed/track.mp3")
    a = launch.config({}, NOW)["audio"]
    check("player labels default to the intended track", a["title"] == "Lilith" and a["artist"] == "Saint Avangeline")

    rule("Assets: /static/launch/ serves its own files and nothing else")
    check("the page's script", launch.asset("/static/launch/launch.js") is not None)
    for bad in ("/static/launch/../launch.py", "/static/launch/%2e%2e/launch.py", "/static/launch/../../server.py",
                "/static/launch/audio/README.txt", "/static/launch/nope.css", "/static/launch/launch.html",
                "/static/launch///attacker/share/x.mp3", "/static/launch/%5c%5cattacker%5cshare%5cx.mp3",
                "/static/launch/audio/%5c%5cattacker%5cs%5cx.mp3", "/static/launch/a%00.js",
                "/static/launch/audio/..%2f..%2flaunch.py", "/static/launch/C:%5cWindows%5cwin.ini"):
        check("not served: " + bad, launch.asset(bad) is None)

    # ── a real tower ─────────────────────────────────────────────────────
    from dashboard import accounts                           # noqa: E402
    from dashboard import server as tower_server             # noqa: E402
    acc = accounts.store()
    PW = "Tower-Pilot-2026!"
    acc.users.create("admin@mantrac.example", "Ama Admin", "ADMIN", password=PW)
    acc.users.create("oper@mantrac.example", "Kofi Operator", "OPERATOR", password=PW)
    acc.users.create("view@mantrac.example", "Esi Viewer", "VIEWER", password=PW)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
    tower_server.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="launch-key-123456")
    BASE = "http://127.0.0.1:{0}".format(PORT)
    time.sleep(0.4)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    def client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())

    def call(opener, method, path, body=None, csrf=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Origin", BASE)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if csrf:
            req.add_header("X-CSRF-Token", csrf)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def sign_in(email):
        o = client()
        st, raw, _ = call(o, "POST", "/api/auth/login", {"email": email, "password": PW})
        return o, json.loads(raw or b"{}").get("csrf")

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def set_launch(dt):
        os.environ["ATA_LAUNCH_AT"] = iso(dt)
        os.environ["ATA_LAUNCH_TZ"] = "UTC"

    PROTECTED_GET = ["/api/state", "/api/evidence", "/api/share", "/api/admin/audit", "/api/export.csv", "/api/report.pdf", "/api/admin/users",
                     "/static/index.html", "/intro"]
    PROTECTED_POST = [("/api/control", {"action": "start"}), ("/api/admin/users", {"email": "x@y.z"}), ("/api/human", {"op": "open"})]

    def protected_closed(tag):
        anon = client()
        st, _r, h = call(anon, "GET", "/")
        bad = []
        for path in PROTECTED_GET:
            code, _r, hh = call(anon, "GET", path)
            if code not in (302, 401) or (code == 302 and not hh.get("Location", "").startswith("/login")):
                bad.append((path, code))
        for path, body in PROTECTED_POST:
            code, _r, _h = call(anon, "POST", path, body)
            if code not in (401, 403):
                bad.append((path, code))
        check(tag + ": every protected page and API still needs a sign-in", not bad, bad)
        return st, h

    for mode in ("before", "after"):
        rule("Signed-in roles and protected routes, {0} launch (accounts on)".format(mode))
        set_launch(datetime.now(UTC) + (timedelta(days=3) if mode == "before" else -timedelta(minutes=1)))
        st, h = protected_closed(mode)
        if mode == "before":
            check("before launch, a signed-out visit to / goes to the launch page",
                  st == 302 and h.get("Location") == "/launch", (st, h.get("Location")))
        else:
            check("after launch, a signed-out visit to / goes to sign-in as before",
                  st == 302 and h.get("Location", "").startswith("/login"), (st, h.get("Location")))
        st, raw, h = call(client(), "GET", "/launch")
        check(mode + ": /launch is public and strict (CSP: own scripts only, no framing)",
              st == 200 and b"ATLAS" in raw and "script-src 'self'" in (h.get("Content-Security-Policy") or "")
              and "frame-ancestors 'none'" in h.get("Content-Security-Policy", ""), (st, h.get("Content-Security-Policy")))
        st, raw, _h = call(client(), "GET", "/api/launch")
        check(mode + ": /api/launch says " + ("countdown" if mode == "before" else "launched"),
              st == 200 and json.loads(raw)["state"] == ("countdown" if mode == "before" else "launched"))
        st, raw, _h = call(client(), "GET", "/login")
        check(mode + ": the real sign-in page is always reachable", st == 200 and b"Sign in" in raw)
        view, vc = sign_in("view@mantrac.example")
        st, raw, _h = call(view, "GET", "/")
        check(mode + ": a signed-in Viewer gets the dashboard, not the launch page", st == 200 and b"launch.js" not in raw)
        check(mode + ": a Viewer reads the state", call(view, "GET", "/api/state")[0] == 200)
        check(mode + ": a Viewer still cannot start a run", call(view, "POST", "/api/control", {"action": "start"}, vc)[0] == 403)
        check(mode + ": a Viewer still cannot manage users", call(view, "GET", "/api/admin/users")[0] == 403)
        oper, oc = sign_in("oper@mantrac.example")
        check(mode + ": an Operator still cannot manage users", call(oper, "GET", "/api/admin/users")[0] == 403)
        adm, ac = sign_in("admin@mantrac.example")
        check(mode + ": an Admin can", call(adm, "GET", "/api/admin/users")[0] == 200)
        check(mode + ": a forged POST without the page's CSRF token is refused",
              call(adm, "POST", "/api/admin/users", {"email": "z@y.z"})[0] == 403)

    rule("Access-key mode (accounts off)")
    os.environ["DASHBOARD_ACCOUNTS"] = "0"
    set_launch(datetime.now(UTC) + timedelta(days=3))
    st, _r, h = call(client(), "GET", "/")
    check("before launch: / without the key is refused as always (the launch page's way in is personal sign-in)",
          st == 401)
    check("before launch: the API still needs the key", call(client(), "GET", "/api/state")[0] == 401)
    check("the launch page itself needs no key", call(client(), "GET", "/launch")[0] == 200)
    check("...nor its logo", call(client(), "GET", "/static/brand/mantrac-logo.png")[0] == 200)
    check("...but no other brand or dashboard file", call(client(), "GET", "/static/index.html")[0] == 401)
    set_launch(datetime.now(UTC) - timedelta(minutes=1))
    st, _r, _h = call(client(), "GET", "/")
    check("after launch: / without the key is refused as before", st == 401)
    check("with the key: the dashboard as before", call(client(), "GET", "/?key=launch-key-123456")[0] == 200)
    os.environ["DASHBOARD_ACCOUNTS"] = "auto"

    rule("Soundtrack and asset paths through the tower (as IIS forwards them)")
    os.environ["ATA_LAUNCH_AUDIO"] = "ct-test-tone.wav"
    cfg = json.loads(call(client(), "GET", "/api/launch")[1])
    check("a present, licensed file is offered", cfg["audio"]["url"] == "/static/launch/audio/ct-test-tone.wav")
    st, raw, h = call(client(), "GET", cfg["audio"]["url"], headers={"Range": "bytes=0-99"})
    check("it streams in ranges, as browsers ask for media", st == 206 and len(raw) == 100 and
          h.get("Content-Type", "").startswith("audio/"), (st, h.get("Content-Type")))
    import re
    page = (launch.LAUNCH_DIR / "launch.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(/[^"]*)"', page)
    check("every asset the page names is an absolute same-site path", refs and all(r.startswith("/") for r in refs), refs)
    broken = [(r, call(client(), "GET", r)[0]) for r in refs if r.startswith("/static/")]
    check("each one is served to a signed-out visitor", all(code == 200 for _r, code in broken), broken)
    st, _r, h = call(client(), "GET", "/static/launch/launch.js")
    check("scripts go out as JavaScript, whatever the Windows registry says",
          h.get("Content-Type", "").startswith("text/javascript"), h.get("Content-Type"))
    check("the backend cannot be read through the launch folder",
          all(call(client(), "GET", p)[0] == 404 for p in
              ("/static/launch/a%00.js", "/static/launch/%5c%5cattacker%5cshare%5cx.mp3")) and
          all(call(client(), "GET", p)[0] == 404 for p in
              ("/static/launch/../server.py", "/static/launch/%2e%2e/launch.py", "/static/launch/%2e%2e/%2e%2e/accounts.py")))
    webcfg = (Path(HERE) / "deploy/iis/web.config").read_text()
    check("IIS forwards every path unchanged, /launch and /static/launch/ included",
          'url="http://127.0.0.1:8787/{R:1}"' in webcfg and '<match url="(.*)" />' in webcfg)
    check("check_iis.ps1 checks the launch page through the domain",
          "/launch" in (Path(HERE) / "deploy/iis/check_iis.ps1").read_text())

    # ── the page in a browser ────────────────────────────────────────────
    from playwright.sync_api import sync_playwright          # noqa: E402
    exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
    with sync_playwright() as p:
        # Playwright turns autoplay on by default; take that away so the
        # browser behaves as a normal one does.
        browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None,
                                    ignore_default_args=["--autoplay-policy=no-user-gesture-required"],
                                    args=["--autoplay-policy=user-gesture-required"])
        problems = []

        def open_page(w=1440, h=900, motion="no-preference", strict_autoplay=False):
            pg = browser.new_page(viewport={"width": w, "height": h}, reduced_motion=motion)
            if strict_autoplay:
                pg.add_init_script("""(() => {
                  const real = HTMLMediaElement.prototype.play;
                  HTMLMediaElement.prototype.play = function(){
                    if (!navigator.userActivation.hasBeenActive)
                      return Promise.reject(new DOMException('needs a gesture', 'NotAllowedError'));
                    return real.call(this);
                  };
                })()""")
            pg.on("pageerror", lambda e: problems.append(str(e)))
            pg.on("console", lambda m: problems.append(m.text) if m.type == "error"
                  and "Failed to load resource" not in m.text else None)
            # Every failed request is named; the one deliberate 401 below is expected.
            pg.on("response", lambda r: problems.append("{0} {1}".format(r.status, r.url))
                  if r.status >= 400 and not r.url.endswith("/api/state") else None)
            pg.goto(BASE + "/launch")
            # A selector wait, not page.evaluate: Playwright's evaluate counts as
            # a user gesture, which would let the music start on its own.
            pg.wait_for_selector("body[data-state]", state="attached", timeout=5000)
            return pg

        rule("Browser: countdown, zone, launch moment")
        os.environ["ATA_LAUNCH_AT"] = (datetime.now(UTC) + timedelta(days=12, hours=5, minutes=30, seconds=20)) \
            .astimezone(timezone(timedelta(hours=2))).isoformat(timespec="seconds")
        os.environ["ATA_LAUNCH_TZ"] = "Africa/Cairo"
        pg = open_page()
        pg.wait_for_timeout(2600)
        vals = [pg.text_content(i) for i in ("#cdD", "#cdH", "#cdM")]
        check("the countdown shows 12 d 05 h 30 m from the server's moment", vals == ["12", "05", "30"], vals)
        s1 = pg.text_content("#cdS"); pg.wait_for_timeout(1100); s2 = pg.text_content("#cdS")
        check("seconds tick", s1 != s2, (s1, s2))
        when = pg.text_content("#whenLabel")
        check("the moment is shown in its zone, with the offset", "Cairo time" in when and "UTC+02:00" in when, when)
        check("before launch the sign-in action is not shown", pg.is_hidden("#ready"))
        check("nothing configured, nothing plays", "is-playing" not in (pg.get_attribute("#sound", "class") or ""))
        pg.close()

        set_launch(datetime.now(UTC) + timedelta(seconds=3))
        pg = open_page()
        check("3 s before: counting", pg.is_visible("#countdown"))
        pg.wait_for_selector("#ready:not([hidden])", timeout=7000)
        check("at zero: the launch-ready state and the sign-in action appear",
              pg.is_hidden("#countdown") and pg.text_content("#kicker") == "Now open"
              and pg.get_attribute("#enter", "href") == "/login")
        pg.wait_for_timeout(2600)
        if SHOTS:
            pg.screenshot(path=os.path.join(SHOTS, "launch_2_launched_desktop.png"))
        status = pg.evaluate("fetch('/api/state').then(r => r.status)")
        check("the page being 'open' opens nothing: the API still answers 401", status == 401, status)
        pg.click("#enter")
        pg.wait_for_url("**/login", timeout=5000)
        check("'Continue to sign in' leads to the real sign-in page", "Sign in" in pg.content())
        pg.close()

        os.environ["ATA_LAUNCH_AT"] = (datetime.now(UTC) + timedelta(days=2)).astimezone(
            timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="seconds")
        os.environ["ATA_LAUNCH_TZ"] = "Asia/Kolkata"
        pg = open_page()
        check("a half-hour zone is named right", "Kolkata time (UTC+05:30)" in pg.text_content("#whenLabel"),
              pg.text_content("#whenLabel"))
        pg.close()
        os.environ["ATA_LAUNCH_AT"] = "2026-11-01T09:00:00"          # no offset
        pg = open_page()
        check("a refused date: no countdown, sign-in available, page intact",
              pg.is_hidden("#countdown") and pg.is_visible("#enter"))
        pg.close()

        rule("Browser: the soundtrack, with no player on screen")
        playing = "document.getElementById('sound').classList.contains('is-playing')"
        set_launch(datetime.now(UTC) + timedelta(days=9, hours=3))
        os.environ.pop("ATA_LAUNCH_AUDIO", None)
        pg = open_page()
        pg.wait_for_timeout(500)
        check("no soundtrack configured: nothing shown, nothing plays, page intact",
              pg.is_hidden("#sound") and pg.text_content("#audioStatus") == "Soundtrack unavailable"
              and pg.is_visible("#countdown"))
        check("there is no player bar, play button or volume slider",
              pg.locator("#player, #play, #vol, input[type=range]").count() == 0)
        pg.close()
        os.environ["ATA_LAUNCH_AUDIO"] = "ct-test-broken.mp3"
        pg = open_page()
        pg.mouse.click(300, 300)
        until(pg, "document.getElementById('audioStatus').textContent === 'Soundtrack unavailable'")
        check("a file that will not play: the switch disappears, page unaffected",
              pg.is_hidden("#sound") and pg.is_visible("#countdown"))
        pg.close()
        os.environ["ATA_LAUNCH_AUDIO"] = "ct-test-tone.wav"
        # Headless Chromium applies its autoplay policy unreliably (and lets a
        # site it was clicked on before autoplay). This page gets a browser
        # that refuses sound exactly as Chrome and Edge do on a first visit:
        # NotAllowedError until the visitor has clicked or pressed a key.
        pg = open_page(strict_autoplay=True)
        pg.wait_for_timeout(1500)
        check("browser blocks sound before a click: it waits, with a hint, and does not force it",
              pg.locator("#sound.is-playing").count() == 0 and pg.is_visible("#soundHint"),
              pg.get_attribute("#sound", "class"))
        if SHOTS:
            pg.wait_for_timeout(2500)
            pg.screenshot(path=os.path.join(SHOTS, "launch_1_waiting_desktop.png"))
        pg.mouse.click(700, 120)
        until(pg, playing)
        check("the first click anywhere starts the music", pg.text_content("#audioStatus") == "Music playing")
        pg.wait_for_timeout(900)
        check("the hint goes once it plays", pg.evaluate(
            "getComputedStyle(document.getElementById('soundHint')).opacity") in ("0",))
        pg.click("#snd")
        until(pg, "!" + playing, 3000)
        check("the corner switch stops it", pg.get_attribute("#snd", "aria-pressed") == "false")
        pg.mouse.click(700, 120)
        pg.wait_for_timeout(600)
        check("...and another click elsewhere does not restart it", not pg.evaluate(playing))
        pg.keyboard.press("Tab")
        pg.wait_for_timeout(300)
        check("Tab moving the focus does not start it", not pg.evaluate(playing))
        pg.click("#snd")
        until(pg, playing)
        check("the switch starts it again", pg.get_attribute("#snd", "aria-label") == "Stop music")
        pg.evaluate("dispatchEvent(new Event('pagehide'))")
        pg.wait_for_timeout(300)
        check("leaving the page stops the music", not pg.evaluate(playing))
        pg.close()
        free = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None,
                                 args=["--autoplay-policy=no-user-gesture-required"])
        fp = free.new_page()
        fp.goto(BASE + "/launch")
        until(fp, playing, 8000)
        check("where the browser allows it (company policy), it plays by itself, no click",
              fp.text_content("#audioStatus") == "Music playing")
        free.close()

        rule("Browser: phone, tablet, reduced motion")
        for name, w, h in (("phone", 390, 844), ("tablet", 820, 1180)):
            pg = open_page(w, h)
            pg.wait_for_timeout(2600)
            check(name + ": no sideways scrolling", pg.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            box = pg.locator("#snd").bounding_box()
            check(name + ": the sound switch is on screen", box and box["y"] + box["height"] <= h, box)
            check(name + ": the countdown is on the first screen",
                  pg.locator("#cdS").bounding_box()["y"] < h - 40)
            if SHOTS:
                pg.screenshot(path=os.path.join(SHOTS, "launch_4_{0}.png".format(name)))
            pg.close()
        pg = open_page(motion="reduce")
        pg.wait_for_timeout(300)
        check("reduced motion: the greeting is there at once",
              "ATLAS" in pg.text_content("#greeting"))
        check("reduced motion: nothing floats, spins or drifts",
              pg.evaluate("['.atlas svg','.disc','.g1','.lane','.port'].every(s => "
                          "getComputedStyle(document.querySelector(s)).animationName === 'none')"))
        check("motion never blocks the page: the ambient layer takes no clicks",
              pg.evaluate("getComputedStyle(document.querySelector('.ambient')).pointerEvents") == "none")
        pg.close()
        check("no script errors and no Content Security Policy violations", not problems, problems)
        browser.close()
finally:
    for f in (TONE, BROKEN):
        try:
            f.unlink()
        except OSError:
            pass

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
