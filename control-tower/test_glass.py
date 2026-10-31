"""
Apple glass across the platform (/static/glass.css): frosted cards and
tiles, glossy chart bars, bars, the sidebar and menus that really blur what
passes under them, a soft light behind. Styles only, loaded last on the hub,
Clearance and the run page. Muted text keeps 4.5:1 on the glass as it is
drawn, in Light, Charcoal and Midnight; "reduce transparency" makes it solid.

    python test_glass.py
"""
import io
import os
import re
import socket
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="glass_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


from dashboard import clearance as C, server as S  # noqa: E402


def row(bu, step, awb, overdue=None, ontime=1):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta="2026-10-01", overdue=overdue, ontime=ontime, awb=awb, mode="Air", status="x", due="2026-10-01")
    return r


ROWS = [row("EG", "Customs Declaration", "EG-%04d" % i, 10.0, 0) for i in range(40)] + [row("KE", "Completed", "KE-0001")]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
C.load = lambda: DATA
C.escalations = lambda: []

ST = HERE / "dashboard" / "static"
CSS = (ST / "glass.css").read_text(encoding="utf-8")
TAG = '<link rel="stylesheet" href="/static/glass.css">'

print("\n1. ONE SHARED LOOK, ADDED ON TOP")
for page in ("hub.html", "clearance.html", "index.html"):
    html = (ST / page).read_text(encoding="utf-8")
    head = html.split("</head>")[0].replace('<script src="/static/glass.js" defer></script>', "")
    check("{0} loads the glass once, as its last stylesheet".format(page), html.count(TAG) == 1 and head.rstrip().endswith(TAG))
check("Styles only: no script, no import, no web font, no image", "<script" not in CSS and "@import" not in CSS and not re.search(r"url\((?!#lg-)", CSS))
check("Light, Charcoal, Midnight and the device's dark each have their glass",
      ':root[data-theme="dark"]{' in CSS and ':root[data-theme="dark"][data-dark="midnight"]{' in CSS and ":root:not([data-theme])" in CSS)
check("Only floating layers blur (in-page cards stay cheap to draw)",
      "backdrop-filter" not in CSS.split("/* ── frosted cards")[1].split("/* ── floating layers")[0])
check("Reduce transparency: solid, no light behind, no blur", "@media (prefers-reduced-transparency:reduce)" in CSS
      and "html::before{display:none}" in CSS.split("prefers-reduced-transparency")[1])
JS = (ST / "glass.js").read_text(encoding="utf-8")
for page in ("hub.html", "clearance.html", "index.html"):
    html = (ST / page).read_text(encoding="utf-8")
    check("{0} loads the Liquid Glass lens once, after the glass".format(page), html.count('<script src="/static/glass.js" defer></script>') == 1
          and html.index("glass.css") < html.index("glass.js"))
check("The lens: drawn in the page (no image file), Chromium only, off with reduce transparency, fetches and stores nothing",
      "toDataURL" in JS and "Chrome\\/|Edg\\/" in JS and "prefers-reduced-transparency: reduce" in JS
      and "fetch(" not in JS and "localStorage" not in JS and "feDisplacementMap" in JS)
check("Without the lens (another browser) the frosted glass stays: the lens rules all need html.lg",
      all(l.strip().startswith("html.lg") for l in CSS.splitlines() if "url(#lg-" in l))
check("A browser that cannot blur gets solid bars and menus", "@supports not ((backdrop-filter:blur(1px))" in CSS)


def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4
    return .2126 * ch(c[0]) + .7152 * ch(c[1]) + .0722 * ch(c[2])


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def rgb(s):
    m = re.findall(r"[\d.]+", s)
    return tuple(int(float(x)) for x in m[:3])


print("\n2. IN THE BROWSER")
try:
    from playwright.sync_api import sync_playwright
    from PIL import Image
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    SKIPPED.append("browser")
else:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        PORT = so.getsockname()[1]
    S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
    BASE = "http://127.0.0.1:{0}".format(PORT)
    time.sleep(0.4)
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()
        for theme in ("light", "dark", "midnight"):
            pg = br.new_page(viewport={"width": 1440, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','%s')" % theme)
            pg.goto(BASE + "/hub"); pg.wait_for_timeout(2800)
            st = pg.evaluate("""(() => { const c = getComputedStyle(document.querySelector('#strip .chip')), t = getComputedStyle(document.querySelector('.top')),
                s = getComputedStyle(document.querySelector('.side')), card = getComputedStyle(document.querySelector('.card'));
                return {chip: c.backgroundImage, card: card.backgroundImage, top: getComputedStyle(document.querySelector('.top'), '::before').backdropFilter, side: s.backdropFilter, sideBg: s.backgroundColor,
                  amb: getComputedStyle(document.documentElement, '::before').backgroundImage.slice(0, 40),
                  ink3: getComputedStyle(document.querySelector('#strip .chip .n')).color}; })()""")
            check("{0}: cards and number tiles are frosted glass (a translucent fill and a rim of light)".format(theme),
                  st["chip"].count("linear-gradient") == 2 and "rgba" in st["chip"] and st["card"].count("linear-gradient") == 2, st["chip"][:120])
            check("{0}: the sidebar bends what is under it (Liquid Glass lens), the top bar frosts it".format(theme),
                  "blur" in st["top"] and "url(\"#lg-lens\")" in st["side"] and rgb(st["sideBg"]) and "rgba" in st["sideBg"], st)
            fl = pg.evaluate("""(() => { const s = getComputedStyle(document.querySelector('.side')), t = getComputedStyle(document.querySelector('.top'));
                return [document.documentElement.classList.contains('lg'), !!document.querySelector('#lg-defs filter#lg-lens feDisplacementMap'), s.borderTopLeftRadius, t.borderTopLeftRadius,
                  document.scrollingElement.scrollWidth <= innerWidth]; })()""")
            check("{0}: Liquid Glass is on: the lens is in the page, the sidebar and the top bar float as rounded glass".format(theme),
                  fl[0] and fl[1] and fl[2] == "26px" and fl[3] == "22px" and fl[4], fl)
            check("{0}: a soft light sits behind the page".format(theme), "radial-gradient" in st["amb"], st["amb"])
            # muted text on the glass as drawn: sample the tile's own pixels next to its caption
            box = pg.locator('#strip .chip[data-list="all"] .n').bounding_box()
            img = Image.open(io.BytesIO(pg.screenshot())).convert("RGB")
            bg = img.getpixel((int(box["x"] + box["width"] + 6), int(box["y"] + box["height"] / 2)))
            r = ratio(rgb(st["ink3"]), bg)
            check("{0}: muted text keeps 4.5:1 on the glass as drawn ({1:.2f}:1)".format(theme, r), r >= 4.5, (st["ink3"], bg))
            pg.locator("[data-todo-slot] .todo-b").click(); pg.wait_for_timeout(600)
            check("{0}: the To Do menu is thick floating glass".format(theme),
                  "lg-frost" in pg.evaluate("getComputedStyle(document.querySelector('.todo-p')).backdropFilter"))
            pg.keyboard.press("Escape"); pg.mouse.click(700, 880); pg.wait_for_timeout(300)
            pg.locator('#strip .chip[data-list="customs"]').click(); pg.wait_for_timeout(900)
            check("{0}: the list's sheet is glass over a softened page".format(theme),
                  "lg-frost" in pg.evaluate("getComputedStyle(document.querySelector('.dl-b')).backdropFilter")
                  and "blur" in pg.evaluate("getComputedStyle(document.querySelector('.dl')).backdropFilter"))
            pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
            check("{0}: chart bars are glossy".format(theme), "linear-gradient" in pg.evaluate("getComputedStyle(document.querySelector('.gb-row u')).backgroundImage"))
            check("{0}: no page error".format(theme), not errs, errs)
            pg.close()
        pg = br.new_page(viewport={"width": 1440, "height": 900})
        pg.add_init_script("sessionStorage.setItem('ct-intro','1')")
        for h in ("#ata", "#clearance"):
            pg.goto(BASE + "/hub" + h); pg.wait_for_timeout(2000)
            check("A page opened in the hub ({0}) still fits the window: one scroll, not two".format(h),
                  pg.evaluate("document.scrollingElement.scrollHeight <= innerHeight + 1"))
        pg.close()
        def sharp(pg, box):
            img = Image.open(io.BytesIO(pg.screenshot(clip=box))).convert("L")
            w, h = img.size
            px = img.load()
            return sum(abs(px[x + 1, y] - px[x, y]) for y in range(h) for x in range(w - 1)) / (w * h)
        # words are scrolled under each bar; with the bar's own buttons hidden, what shows through is
        # measured with the glass and without it
        for url, bar, target in (("/hub", ".top", ".gb-row"), ("/clearance", ".filters", ".chart text")):
            pg = br.new_page(viewport={"width": 1440, "height": 900})
            pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','dark');localStorage.setItem('ata-theme','dark')")
            pg.goto(BASE + url); pg.wait_for_timeout(2500)
            pg.evaluate("""([bar, target]) => { const b = document.querySelector(bar).getBoundingClientRect(), r = document.querySelector(target).getBoundingClientRect();
                window.scrollBy(0, r.top + r.height / 2 - (b.top + b.height / 2)); }""", [bar, target])
            pg.wait_for_timeout(800)
            pg.add_style_tag(content=bar + " > *{visibility:hidden!important}")
            pg.wait_for_timeout(300)
            bb = pg.locator(bar).bounding_box()
            box = {"x": bb["x"] + 16, "y": bb["y"] + 4, "width": bb["width"] - 32, "height": bb["height"] - 8}
            frosted = sharp(pg, box)
            pg.add_style_tag(content=bar + "::before{display:none!important}"); pg.wait_for_timeout(300)
            bare = sharp(pg, box)
            check("{0}: words that scroll under {1} are frosted, not readable (sharpness {2:.2f}, {3:.2f} without the glass)".format(url, bar, frosted, bare),
                  bare > .2 and frosted < bare * .6, (frosted, bare, box))
            pg.close()
        # Clearance and the run page: the same glass
        pg = br.new_page(viewport={"width": 1440, "height": 900})
        pg.add_init_script("sessionStorage.setItem('ct-intro','1')")
        pg.goto(BASE + "/clearance"); pg.wait_for_timeout(2500)
        check("Clearance: its cards are glass and its sticky filters blur what scrolls under them",
              pg.evaluate("getComputedStyle(document.querySelector('.card')).backgroundImage").count("linear-gradient") == 2
              and "blur" in pg.evaluate("getComputedStyle(document.querySelector('.filters'), '::before').backdropFilter"))
        check("Clearance: chart bars have a light edge", pg.evaluate("(() => { const b = document.querySelector('.chart rect.b'); return !b || getComputedStyle(b).stroke !== 'none'; })()"))
        pg.goto(BASE + "/"); pg.wait_for_timeout(2500)
        check("The run page: cards are glass, the top bar blurs",
              pg.evaluate("getComputedStyle(document.querySelector('.card')).backgroundImage").count("linear-gradient") == 2
              and "blur" in pg.evaluate("getComputedStyle(document.querySelector('.appbar'), '::before').backdropFilter"))
        pg.close()
        # reduce transparency
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        cdp = ctx.new_cdp_session(pg)
        cdp.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-transparency", "value": "reduce"}]})
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        pg.goto(BASE + "/hub"); pg.wait_for_timeout(2500)
        solid = pg.evaluate("""(() => [getComputedStyle(document.querySelector('#strip .chip')).backgroundColor, getComputedStyle(document.querySelector('.top')).backdropFilter,
            getComputedStyle(document.documentElement, '::before').display])()""")
        check("Reduce transparency: solid tiles, no blur, no light behind", solid[0] == "rgb(255, 255, 255)" and solid[1] == "none" and solid[2] == "none", solid)
        ctx.close()
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
