"""
The Hub sign-in moves from credentials.txt to Windows Credential Manager:
read first from Credential Manager, the file only as a fallback, the file
deleted only after the entry reads back the same, nothing secret printed.
Credential Manager is replaced by an in-memory store here; on Windows the
real one is used (advapi32).

    python test_hub_credentials.py
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(HERE))
import hub_credentials as H                                   # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


class Fake(object):
    def __init__(self, corrupt=False):
        self.store, self.corrupt = {}, corrupt

    def read(self, target):
        return self.store.get(target)

    def write(self, target, user, secret):
        self.store[target] = (user, secret + ("x" if self.corrupt else ""))

    def delete(self, target):
        return self.store.pop(target, None) is not None


SECRET = "S3cret!pw"
tmp = Path(tempfile.mkdtemp())
creds = tmp / "credentials.txt"


def write_file():
    creds.write_text("# Hub\nUSERNAME=hub.user\nPASSWORD={0}\n".format(SECRET), encoding="utf-8")


def run(fn, *args, **kw):
    lines = []
    code = fn(*args, out=lines.append, **kw)
    return code, "\n".join(lines)


print("1. OFF WINDOWS")
H._backend = None
if os.name != "nt":
    check("No store off Windows: read() is None, nothing is changed", H.read() is None)
    code, text = run(H.move, creds, ask=lambda q: "y")
    check("move refuses off Windows and says why", code == 1 and "Windows only" in text)

print("2. MOVE")
write_file()
H._backend = Fake()
check("the file is read (USERNAME=/PASSWORD=, comments skipped)", H.read_file(creds) == ("hub.user", SECRET))
check("source() says 'file' before the move", H.source(creds) == "file")
code, text = run(H.move, creds, ask=lambda q: "n")
check("move stores the sign-in and keeps the file when told no", code == 0 and creds.exists()
      and H.read() == ("hub.user", SECRET))
check("Credential Manager is read first once it holds the sign-in", H.source(creds) == "credential-manager")
code, text = run(H.move, creds, ask=lambda q: "y")
check("move deletes the file only when told yes", code == 0 and not creds.exists())
check("no output ever shows the password", SECRET not in text)

print("3. SAFETY")
write_file()
H._backend = Fake(corrupt=True)
code, text = run(H.move, creds, ask=lambda q: "y")
check("an entry that does not read back the same keeps the file", code == 1 and creds.exists() and "kept" in text)
H._backend = Fake()
code, text = run(H.set_interactive, ask=lambda q: "hub.user", secret=(lambda it: (lambda q: next(it)))(iter(["a", "b"])))
check("set refuses two different passwords", code == 1 and H.read() is None)
code, text = run(H.set_interactive, ask=lambda q: "hub.user", secret=(lambda it: (lambda q: next(it)))(iter([SECRET, SECRET])))
check("set stores a typed sign-in (password hidden by getpass)", code == 0 and H.read() == ("hub.user", SECRET)
      and SECRET not in text)
code, text = run(H.status, creds)
check("status names the source and user, never the password", "Credential Manager" in text and SECRET not in text)

print("4. THE TOWER")
import update_eta as A                                         # noqa: E402
A.CREDENTIALS_FILE = tmp / "absent.txt"
check("update_eta.load_credentials takes Credential Manager first", A.load_credentials() == ("hub.user", SECRET))
H._backend = Fake()
try:
    A.load_credentials()
    check("with neither, the error names both places and the fix", False)
except Exception as error:
    check("with neither, the error names both places and the fix",
          "Credential Manager" in str(error) and "STORE_HUB_PASSWORD.bat" in str(error))
write_file()
A.CREDENTIALS_FILE = creds
check("a server not moved yet still works from credentials.txt", A.load_credentials() == ("hub.user", SECRET))

src = (HERE / "hub_credentials.py").read_text(encoding="utf-8")
check("Windows store: advapi32 CredRead/CredWrite, generic entry, local-machine persistence",
      "CredReadW" in src and "CredWriteW" in src and "GENERIC, LOCAL_MACHINE, NOT_FOUND = 1, 2, 1168" in src)
bat = (HERE / "STORE_HUB_PASSWORD.bat").read_text(encoding="utf-8")
check("STORE_HUB_PASSWORD.bat moves the file when it exists, else asks", "hub_credentials move" in bat
      and "hub_credentials set" in bat)

H._backend = None
print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
