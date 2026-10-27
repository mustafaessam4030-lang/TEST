"""
The Logistics Hub sign-in, kept in Windows Credential Manager instead of a
text file.

Windows encrypts the entry for the Windows account that stores it (DPAPI):
other accounts on the server cannot read it, and it is never in a file that
can be copied, zipped or emailed. The tower reads it first; credentials.txt
is the fallback while a server has not moved yet.

    python -m hub_credentials status   where the sign-in comes from (no secret printed)
    python -m hub_credentials move     credentials.txt -> Credential Manager, then delete the file
    python -m hub_credentials set      type the user name and password (password hidden)
    python -m hub_credentials remove   delete the Credential Manager entry

Run it as the Windows account that runs the tower (the task "Mantrac Tower"):
each Windows account has its own Credential Manager.
"""

import getpass
import os
import sys
from pathlib import Path

TARGET = "MantracTower/LogisticsHub"
COMMENT = "Logistics Hub sign-in for the Mantrac ATA Control Tower"
DEFAULT_FILE = Path(r"C:\Automation\credentials.txt")


class _Windows(object):
    """Credential Manager through advapi32 (standard library only)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        self.ct = ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                        ("Comment", wt.LPWSTR), ("LastWritten", FILETIME),
                        ("CredentialBlobSize", wt.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                        ("UserName", wt.LPWSTR)]

        self.CREDENTIALW = CREDENTIALW
        self.api = ctypes.WinDLL("advapi32", use_last_error=True)
        self.api.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        self.api.CredReadW.restype = wt.BOOL
        self.api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wt.DWORD]
        self.api.CredWriteW.restype = wt.BOOL
        self.api.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
        self.api.CredDeleteW.restype = wt.BOOL
        self.api.CredFree.argtypes = [ctypes.c_void_p]

    GENERIC, LOCAL_MACHINE, NOT_FOUND = 1, 2, 1168

    def read(self, target):
        ct = self.ct
        p = ct.POINTER(self.CREDENTIALW)()
        if not self.api.CredReadW(target, self.GENERIC, 0, ct.byref(p)):
            if ct.get_last_error() == self.NOT_FOUND:
                return None
            raise OSError("Credential Manager could not be read (Windows error {0})".format(ct.get_last_error()))
        try:
            c = p.contents
            secret = ct.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
            return c.UserName or "", secret
        finally:
            self.api.CredFree(p)

    def write(self, target, user, secret):
        ct = self.ct
        blob = secret.encode("utf-16-le")
        buf = (ct.c_ubyte * len(blob)).from_buffer_copy(blob)
        c = self.CREDENTIALW()
        c.Type, c.TargetName, c.Comment, c.UserName = self.GENERIC, target, COMMENT, user
        c.CredentialBlobSize, c.CredentialBlob = len(blob), ct.cast(buf, ct.POINTER(ct.c_ubyte))
        c.Persist = self.LOCAL_MACHINE
        if not self.api.CredWriteW(ct.byref(c), 0):
            raise OSError("Credential Manager refused the entry (Windows error {0})".format(ct.get_last_error()))

    def delete(self, target):
        if not self.api.CredDeleteW(target, self.GENERIC, 0):
            if self.ct.get_last_error() != self.NOT_FOUND:
                raise OSError("Credential Manager could not delete the entry (Windows error {0})".format(self.ct.get_last_error()))
            return False
        return True


_backend = None


def backend():
    """The store in use: Windows Credential Manager, or None off Windows."""
    global _backend
    if _backend is None and os.name == "nt":
        _backend = _Windows()
    return _backend


def read():
    """-> (user, password) from Credential Manager, or None when there is none."""
    b = backend()
    if b is None:
        return None
    found = b.read(TARGET)
    if not found or not found[0] or not found[1]:
        return None
    return found


def read_file(path):
    """-> (user, password) from a credentials.txt (USERNAME= / PASSWORD=), or None."""
    path = Path(path)
    if not path.exists():
        return None
    values = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip().upper()] = value.strip()
    user, secret = values.get("USERNAME"), values.get("PASSWORD")
    return (user, secret) if user and secret else None


def source(path=DEFAULT_FILE):
    """Where the tower would take the sign-in from: 'credential-manager', 'file' or None."""
    if read():
        return "credential-manager"
    if read_file(path):
        return "file"
    return None


def _need_store(out):
    if backend() is None:
        out("Credential Manager exists on Windows only; nothing was changed.")
        return False
    return True


def move(path=DEFAULT_FILE, out=print, ask=input):
    """credentials.txt -> Credential Manager; the file is deleted only after the entry reads back the same."""
    if not _need_store(out):
        return 1
    found = read_file(path)
    if not found:
        out("No usable {0} (it needs USERNAME= and PASSWORD= lines). Use: python -m hub_credentials set".format(path))
        return 1
    backend().write(TARGET, found[0], found[1])
    if read() != found:
        out("STOP: the Credential Manager entry did not read back the same. {0} was kept.".format(path))
        return 1
    out("Stored the Hub sign-in for user {0} in Windows Credential Manager ({1}).".format(found[0], TARGET))
    if ask("Delete {0} now? It is no longer needed. [y/N] ".format(path)).strip().lower() in ("y", "yes"):
        os.remove(str(path))
        out("Deleted {0}. The tower now reads the sign-in from Credential Manager.".format(path))
    else:
        out("{0} was kept. Credential Manager is read first; delete the file when you are ready.".format(path))
    return 0


def set_interactive(out=print, ask=input, secret=getpass.getpass):
    if not _need_store(out):
        return 1
    user = ask("Hub user name: ").strip()
    first, second = secret("Hub password (hidden): "), secret("Again: ")
    if not user or not first:
        out("Nothing stored: the user name and password are both needed.")
        return 1
    if first != second:
        out("Nothing stored: the two passwords differ.")
        return 1
    backend().write(TARGET, user, first)
    if read() != (user, first):
        out("STOP: the entry did not read back the same.")
        return 1
    out("Stored the Hub sign-in for user {0} in Windows Credential Manager.".format(user))
    return 0


def status(path=DEFAULT_FILE, out=print):
    where = source(path)
    if where == "credential-manager":
        out("Hub sign-in: Windows Credential Manager, user {0} ({1}).".format(read()[0], TARGET))
        if Path(path).exists():
            out("{0} still exists; it is not used. Delete it: python -m hub_credentials move".format(path))
    elif where == "file":
        out("Hub sign-in: {0} (a text file). Move it: python -m hub_credentials move".format(path))
    else:
        out("No Hub sign-in found. Store it: python -m hub_credentials set")
    return 0 if where else 1


def remove(out=print):
    if not _need_store(out):
        return 1
    out("Removed." if backend().delete(TARGET) else "There was no entry.")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_FILE
    if cmd == "status":
        return status(path)
    if cmd == "move":
        return move(path)
    if cmd == "set":
        return set_interactive()
    if cmd == "remove":
        return remove()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
