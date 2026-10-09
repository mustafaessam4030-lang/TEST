"""
Web search for ATLAS on this PC: a private SearXNG, installed and started
without WSL or Docker.

    python -m intelligence.searxng_local install    once (SETUP_WEB_SEARCH.bat)
    python -m intelligence.searxng_local run        in the foreground
    python -m intelligence.searxng_local status

What install does: downloads one fixed, tested SearXNG version from GitHub
(open source, AGPL), unpacks it into <tower>\\searxng\\, gives it its own
Python environment there and installs its packages. Nothing else on the PC
changes; deleting the searxng folder removes it.

What run does, for Windows and company networks:
  - SearXNG imports "pwd", which Windows does not have; it is only used for
    an optional cache this setup does not turn on, so a stand-in is given.
  - the company may inspect HTTPS with its own certificate, which Windows
    trusts but SearXNG would not: the certificates Windows trusts are added
    to SearXNG's list (they are public certificates, never keys).
  - a proxy set in Windows' Internet Settings is passed on to SearXNG.
  - it listens on 127.0.0.1:8888 only: nobody else on the network can use it.

The tower (intelligence/autoconfig.py) starts it by itself, in the
background, when it is installed and not already running.
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

VERSION = "6671d89bede8c9fc108b17bb98916170f5657650"     # tested with ATLAS
DOWNLOAD = "https://github.com/searxng/searxng/archive/{0}.zip".format(VERSION)
TOWER = Path(__file__).resolve().parent.parent
HOME = TOWER / "searxng"
SRC = HOME / "src"
VENV = HOME / "venv"
PORT = 8888
TEMPLATE = TOWER / "deploy" / "searxng" / "settings.yml"


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed():
    return venv_python().exists() and (SRC / "searx" / "webapp.py").exists()


def say(text):
    print("  " + text, flush=True)


# ── install ──────────────────────────────────────────────────────────────

def install(from_zip=None):
    """Download (or take `from_zip`), unpack, create the environment, install."""
    HOME.mkdir(exist_ok=True)
    archive = Path(from_zip) if from_zip else HOME / "searxng-{0}.zip".format(VERSION[:7])
    if not from_zip and not archive.exists():
        say("Downloading SearXNG {0} from GitHub...".format(VERSION[:7]))
        partial = archive.with_suffix(".part")
        with urllib.request.urlopen(DOWNLOAD, timeout=120) as response, \
                open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(archive)
    say("Unpacking...")
    if SRC.exists():
        shutil.rmtree(SRC)
    with tempfile.TemporaryDirectory(dir=HOME) as tmp, zipfile.ZipFile(archive) as zf:
        zf.extractall(tmp)
        top = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(top) != 1 or not (top[0] / "searx" / "webapp.py").exists():
            raise RuntimeError("the download is not a SearXNG source archive")
        shutil.move(str(top[0]), str(SRC))
    if not venv_python().exists():
        say("Creating its own Python environment (searxng\\venv)...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    say("Installing its packages (a few minutes)...")
    subprocess.run([str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check",
                    "-q", "-r", str(SRC / "requirements.txt")], check=True)
    say("SearXNG installed in {0}".format(HOME))


# ── run ──────────────────────────────────────────────────────────────────

def _ca_bundle():
    """certifi's list plus every certificate Windows trusts, in one file."""
    import ssl
    pems = []
    try:
        import certifi
        pems.append(Path(certifi.where()).read_text(encoding="ascii", errors="ignore"))
    except Exception:
        pass
    if hasattr(ssl, "enum_certificates"):                       # Windows only
        for store in ("ROOT", "CA"):
            try:
                for cert, encoding, _trust in ssl.enum_certificates(store):
                    if encoding == "x509_asn":
                        pems.append(ssl.DER_cert_to_PEM_cert(cert))
            except OSError:
                pass
    else:
        default = ssl.get_default_verify_paths().cafile
        if default and Path(default).exists():
            pems.append(Path(default).read_text(encoding="ascii", errors="ignore"))
    if not pems:
        return None
    path = HOME / "ca-bundle.pem"
    path.write_text("\n".join(pems), encoding="ascii")
    return str(path)


def _settings():
    """This PC's settings file: the template + certificates + proxy."""
    import yaml                                         # SearXNG's own dependency
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data.setdefault("server", {}).update({"bind_address": "127.0.0.1", "port": PORT,
                                          "limiter": False, "public_instance": False})
    outgoing = data.setdefault("outgoing", {})
    bundle = _ca_bundle()
    if bundle:
        outgoing["verify"] = bundle
    proxies = urllib.request.getproxies()               # environment or Windows settings
    chosen = proxies.get("https") or proxies.get("http")
    if chosen:
        if "://" not in chosen:
            chosen = "http://" + chosen
        outgoing["proxies"] = {"all://": [chosen]}
    path = HOME / "settings.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def run():
    """Run SearXNG in this process (the environment's Python)."""
    try:
        import pwd  # noqa: F401
    except ImportError:
        # Windows: only SearXNG's optional Valkey cache uses pwd, and it is
        # not configured here; a stand-in lets the module import.
        import types
        stand_in = types.ModuleType("pwd")

        def getpwuid(uid):
            raise KeyError(uid)

        stand_in.getpwuid = getpwuid
        sys.modules["pwd"] = stand_in
    os.environ["SEARXNG_SETTINGS_PATH"] = str(_settings())
    os.environ.setdefault("SEARXNG_SECRET", secrets.token_hex(24))
    os.chdir(SRC)
    sys.path.insert(0, str(SRC))
    import runpy
    runpy.run_module("searx.webapp", run_name="__main__")


def start():
    """Start it in the background, with no window. -> True when launched."""
    if not installed():
        return False
    log = open(HOME / "searxng.log", "ab")
    flags = 0
    if os.name == "nt":
        flags = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
    subprocess.Popen([str(venv_python()), "-m", "intelligence.searxng_local", "run"],
                     cwd=str(TOWER), stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags,
                     close_fds=True)
    return True


def status():
    from . import autoconfig
    ok, detail = autoconfig.check_search("http://127.0.0.1:{0}".format(PORT))
    say("installed: {0}".format("yes, in " + str(HOME) if installed() else "no"))
    say("running:   {0}".format(detail))
    return ok


def main(argv):
    command = argv[1] if len(argv) > 1 else "status"
    if command == "install":
        from_zip = argv[argv.index("--from-zip") + 1] if "--from-zip" in argv else None
        install(from_zip)
        say("Starting it...")
        start()
        for _ in range(60):
            time.sleep(2)
            from . import autoconfig
            ok, detail = autoconfig.check_search("http://127.0.0.1:{0}".format(PORT))
            if ok:
                say("Web search works: " + detail)
                return 0
        say("SearXNG did not answer yet. Its log: {0}".format(HOME / "searxng.log"))
        return 1
    if command == "run":
        run()
        return 0
    return 0 if status() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
