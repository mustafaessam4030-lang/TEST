"""Share Luma with your team on the company network: ``python -m eka.share``.

The normal start only answers this computer (127.0.0.1). This mode listens on the
network so colleagues can open it from their own browsers, and protects it with a
team key: the link it prints carries the key, and anyone without it is asked for it.

* The key is EKA_API_TOKEN from config.env if you set one, otherwise a random key
  that is created once and kept in <data>/team-key.txt (delete that file for a new one).
* The link is printed and saved to <data>/team-link.txt. The key travels after the
  "#" in the link, which browsers never send to the server, so it is not logged.
* Documents, the index and the language model stay on this computer; colleagues only
  see the answers and the cited pages, as on this computer.
"""
from __future__ import annotations

import os
import secrets
import socket
import threading
import webbrowser
from pathlib import Path

from .config import load_config_file, load_settings


def lan_addresses() -> list[str]:
    """This computer's IPv4 addresses on the local network."""
    found: list[str] = []
    try:
        # Selecting a route sends nothing; it only reveals which interface reaches the network.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith(("127.", "169.254."))]


def team_key(data_dir: Path) -> str:
    configured = os.environ.get("EKA_API_TOKEN", "").strip()
    if configured:
        return configured
    path = data_dir / "team-key.txt"
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(9)
    path.write_text(key + "\n", encoding="utf-8")
    return key


def links(port: int, key: str) -> list[str]:
    hosts = [socket.gethostname()] + lan_addresses()
    return ["http://{0}:{1}/#key={2}".format(h, port, key) for h in dict.fromkeys(hosts) if h]


def main():
    # config.env says 127.0.0.1 for the normal start; sharing needs the network, unless a
    # specific address was asked for here (EKA_SHARE_HOST)
    os.environ["EKA_HOST"] = os.environ.get("EKA_SHARE_HOST", "0.0.0.0")
    load_config_file()
    settings = load_settings()
    settings.ensure_dirs()
    key = team_key(settings.data_dir)
    os.environ["EKA_API_TOKEN"] = key
    os.environ["EKA_OPEN_BROWSER"] = "0"
    team = links(settings.port, key)
    text = "\n".join([
        "Luma team link (copy one into any browser on the company network):",
        *("  " + link for link in team),
        "",
        "Team key: {0}   (asked for if someone opens the address without it)".format(key),
        "This computer: http://127.0.0.1:{0}/#key={1}".format(settings.port, key),
    ])
    (settings.data_dir / "team-link.txt").write_text(text + "\n", encoding="utf-8")
    bar = "=" * 72
    print("\n".join(["", bar, text, "",
                     "Keep this window open while the team is testing. Close it to stop sharing.",
                     "Windows may ask whether Python can use the network: choose Private networks, Allow.",
                     "The link is also saved in " + str(settings.data_dir / "team-link.txt"), bar, ""]), flush=True)
    if os.environ.get("EKA_SHARE_OPEN_BROWSER", "1") not in ("0", "false", "no"):
        local = "http://127.0.0.1:{0}/#key={1}".format(settings.port, key)
        threading.Timer(2.5, lambda: webbrowser.open(local)).start()
    from .__main__ import main as run
    run()


if __name__ == "__main__":
    main()
