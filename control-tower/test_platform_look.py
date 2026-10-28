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

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
