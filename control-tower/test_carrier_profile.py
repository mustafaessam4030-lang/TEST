"""
The carriers' own browser profile, so a passed "are you human?" check is
remembered between runs — and the limits agreed with the operator for it.

    python test_carrier_profile.py

Offline: the browser is a stand-in. (Checked live on 8 Oct 2026: Turkish
Cargo's four lasting cookies came back on the next run; session cookies did
not, as in any browser.)
"""

import os
import stat
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(tempfile.mkdtemp(prefix="ct_profile_"))
os.environ["ATA_BASE_FOLDER"] = str(WORK)
os.environ["ATA_CARRIER_PROFILE_DIR"] = str(WORK / "carrier_profile")
os.environ.pop("ATA_CARRIER_PROFILE", None)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

import update_eta as A                                          # noqa: E402

PASS, FAIL = [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


class Chromium:
    """Records how the browser was asked for."""

    def __init__(self, persistent_fails=False):
        self.calls, self.persistent_fails = [], persistent_fails

    def launch_persistent_context(self, user_data_dir, **options):
        self.calls.append(("persistent", user_data_dir, options))
        if self.persistent_fails:
            raise RuntimeError("profile is in use by another browser")
        return "persistent-context"

    def launch(self, **options):
        self.calls.append(("fresh", None, options))

        class Browser:
            def new_context(self, **kw):
                return ("fresh-context", kw)
        return Browser()


class Playwright:
    def __init__(self, **kw):
        self.chromium = Chromium(**kw)


print("=" * 72)
print("1. CARRIER PAGES OPEN IN A PROFILE THAT IS KEPT")
print("=" * 72)
pw = Playwright()
context, kept = A.open_carrier_context(pw)
kind, folder, options = pw.chromium.calls[0]
check("A persistent profile is opened, in the configured folder",
      kept and kind == "persistent" and folder == str(WORK / "carrier_profile"))
check("...with the run's own browser settings (headed Edge)",
      options.get("channel") == "msedge" and options.get("headless") is False)
check("...and downloads refused", options.get("accept_downloads") is False)
check("...and no Hub credentials in it", "http_credentials" not in options)
if os.name != "nt":
    mode = stat.S_IMODE(os.stat(A.CARRIER_PROFILE_DIR).st_mode)
    check("The folder is readable by this account only (0700)", mode == 0o700, oct(mode))
check("Windows: inheritance removed, this account and SYSTEM only",
      '"/inheritance:r"' in SRC and "SYSTEM:(OI)(CI)F" in SRC)
check("It says when it was made and how to forget it",
      "reset_carrier_profile.bat" in (A.CARRIER_PROFILE_DIR / "created.txt").read_text())

print()
print("=" * 72)
print("2. THE HUB IS NEVER IN IT")
print("=" * 72)
main = SRC.split("def main():")[1]
check("The Hub signs in on the run's own context (with its credentials)",
      "context = browser.new_context(**hub_context_options(username, password))" in main
      and "internal_page = context.new_page()" in main)
check("DHL, Qatar and every portal tab open in the carriers' context",
      "dhl_page = carrier_context.new_page()" in main
      and "qatar_page = carrier_context.new_page()" in main
      and "provider_pages[_portal] = carrier_context.new_page()" in main)
check("A tab opened later follows the carrier tabs, not the Hub's",
      "provider_pages[provider] = anchor.context.new_page()" in SRC)
check("The carriers' browser is closed with the run",
      "carrier_context.close()" in main)

print()
print("=" * 72)
print("3. BOUNDED, AND WITH AN OFF SWITCH")
print("=" * 72)
born = A.CARRIER_PROFILE_DIR / "created.txt"
(A.CARRIER_PROFILE_DIR / "Cookies").write_text("stand-in")
old = time.time() - (A.CARRIER_PROFILE_DAYS + 5) * 86400
os.utime(born, (old, old))
A.open_carrier_context(Playwright())
check("A profile older than {0} days is deleted and starts again empty".format(
      A.CARRIER_PROFILE_DAYS), not (A.CARRIER_PROFILE_DIR / "Cookies").exists()
      and born.exists())
pw = Playwright(persistent_fails=True)
context, kept = A.open_carrier_context(pw)
check("A profile already in use (another run) -> a fresh browser, not a crash",
      kept is False and pw.chromium.calls[-1][0] == "fresh")
A.CARRIER_PROFILE_ON = False
pw = Playwright()
context, kept = A.open_carrier_context(pw)
check("ATA_CARRIER_PROFILE=0 -> a fresh browser every run, as before",
      kept is False and [c[0] for c in pw.chromium.calls] == ["fresh"])
A.CARRIER_PROFILE_ON = True
check("reset_carrier_profile.bat deletes the profile and nothing else",
      "rmdir /s /q \"%PROFILE%\"" in (HERE / "reset_carrier_profile.bat").read_text())

print()
print("=" * 72)
print("4. NEVER READ, PRINTED OR SHIPPED")
print("=" * 72)
block = SRC.split("def _private_folder")[1].split("def hub_context_options")[0]
check("Nothing in the profile code reads a cookie", ".cookies(" not in block
      and "storage_state" not in block)
log = Path(A.LOG_FILE).read_text(encoding="utf-8") if Path(A.LOG_FILE).exists() else ""
check("The run log names the folder, never a cookie",
      "carrier_profile" in log and "stand-in" not in log)
sys.path.insert(0, str(HERE))
import make_release                                             # noqa: E402
check("Never in a release ZIP",
      make_release.excluded("carrier_profile/Default/Cookies"))
check("Never in Git", "carrier_profile/" in (HERE / ".gitignore").read_text())
collector = (HERE / "collect_diagnostics.py").read_text(encoding="utf-8")
check("Never in the diagnostics zip (it collects named files and logs/*.txt only)",
      "carrier_profile" not in collector and '"*.txt"' in collector)

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
