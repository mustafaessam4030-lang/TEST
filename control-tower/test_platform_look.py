"""
The platform looks like one product: /static/platform.css carries the
console's own final design tokens (index.html: its base tokens, then its
POLISH LAYER), and pages outside the console (Clearance) use it. This fails
if a token drifts between the two, or a page stops using the shared look.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "dashboard", "static")
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as handle:
        return handle.read()


def tokens(css, selector):
    """Final custom-property values for one selector, later blocks winning."""
    out = {}
    for m in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", css):
        body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body + ";"):
            out[name] = " ".join(value.split())
    return out


index = read("index.html")
index_css = "\n".join(re.findall(r"<style>(.*?)</style>", index, re.S))
shared = read("platform.css")
for selector, label in ((":root", "light"), (':root[data-theme="dark"]', "dark")):
    ours, theirs = tokens(shared, selector), tokens(index_css, selector)
    if selector == ":root":           # ":root{" must not also match ':root[data-theme…'
        ours = tokens(re.sub(r':root\[data-theme="dark"\]\s*\{[^}]*\}', "", shared), ":root")
        theirs = tokens(re.sub(r':root\[data-theme="dark"\]\s*\{[^}]*\}', "", index_css), ":root")
    differ = {k: (v, theirs.get(k)) for k, v in ours.items() if k in theirs and theirs[k] != v}
    missing = [k for k in ours if k not in theirs]
    check("{0} tokens in platform.css equal the console's ({1} checked)".format(label, len(ours)),
          not differ and not missing and len(ours) > 30, differ or missing)

cl = read("clearance.html")
check("Clearance loads the shared look", '<link rel="stylesheet" href="/static/platform.css">' in cl)
check("…sets the theme before the first paint, from the console's saved choice",
      cl.index("localStorage.getItem('ata-theme')") < cl.index("<style>"))
check("…has the platform's top bar, theme switch and sign-out",
      '<header class="appbar">' in cl and 'data-theme-set="dark"' in cl and 'id="pbOut"' in cl)
style = cl[cl.index("<style>"):cl.index("</style>")]
fixed = [c for c in re.findall(r"#[0-9A-Fa-f]{3,6}\b", re.sub(r":root(\[[^\]]*\])?\{[^}]*\}", "", style))]
check("…and no fixed colours left in its styles (all from the tokens)", not fixed, fixed[:8])
check("page-to-page transitions are on in both, for navigations only",
      "@view-transition{navigation:auto;types:nav}" in shared and
      "@view-transition{navigation:auto;types:nav}" in index and
      "html:active-view-transition-type(nav) .appbar {view-transition-name:appbar}" in index)

print("\nLighter pages: compressed, kept by the browser")
import gzip as _gz                                             # noqa: E402
import socket as _sock                                         # noqa: E402
import tempfile as _tf                                         # noqa: E402
import time as _t                                              # noqa: E402
import urllib.error as _ue                                     # noqa: E402
import urllib.request as _ur                                   # noqa: E402
_T = _tf.mkdtemp(prefix="look_")
os.environ.update({"DASHBOARD_ACCOUNTS": "off", "ATA_RUNTIME_DIR": _T, "ATLAS_INTEL_DIR": _T + "/i", "PO_AUTO": "0",
                   "ATLAS_AI": "0", "DASHBOARD_ACCESS_KEY_FILE": _T + "/k", "PO_DATA_DIR": _T + "/po"})
sys.path.insert(0, HERE)
from dashboard import server as _server                        # noqa: E402
_s = _sock.socket(); _s.bind(("127.0.0.1", 0)); _port = _s.getsockname()[1]; _s.close()
_server.start(port=_port, open_browser=False, host="127.0.0.1")
_t.sleep(0.4)


def _get(path, **h):
    try:
        r = _ur.urlopen(_ur.Request("http://127.0.0.1:%d%s" % (_port, path), headers=h), timeout=20)
        return r.status, r.read(), r.headers
    except _ue.HTTPError as e:
        return e.code, e.read(), e.headers


st, body, hd = _get("/static/clearance.html", **{"Accept-Encoding": "gzip"})
plain = read("clearance.html").encode("utf-8")
check("a page goes compressed when the browser accepts it, and unpacks to the same bytes",
      st == 200 and hd.get("Content-Encoding") == "gzip" and _gz.decompress(body) == plain and len(body) < len(plain) / 2)
st2, body2, hd2 = _get("/static/clearance.html")
check("…and plain when it does not, with its own validator",
      st2 == 200 and hd2.get("Content-Encoding") is None and body2 == plain and hd2.get("ETag") != hd.get("ETag"))
st3, _b3, hd3 = _get("/static/clearance.html", **{"Accept-Encoding": "gzip", "If-None-Match": hd["ETag"]})
check("a repeat visit gets 'not modified', with Vary", st3 == 304 and hd3.get("Vary") == "Accept-Encoding", (st3, dict(hd3)))
st4, _b4, _h4 = _get("/static/clearance.html", **{"If-None-Match": hd["ETag"]})
check("…but never the compressed version's validator for a plain request", st4 == 200)
check("pages are kept privately and revalidated", hd.get("Cache-Control") == "private, no-cache")

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
