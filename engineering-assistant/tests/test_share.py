"""Sharing with the team: the key, the links and the server settings that sharing needs."""
import os

from eka import share


def test_team_key_is_created_once_and_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("EKA_API_TOKEN", raising=False)
    first = share.team_key(tmp_path)
    assert len(first) >= 12 and share.team_key(tmp_path) == first
    assert (tmp_path / "team-key.txt").read_text().strip() == first


def test_a_configured_key_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("EKA_API_TOKEN", "our-own-key")
    assert share.team_key(tmp_path) == "our-own-key" and not (tmp_path / "team-key.txt").exists()


def test_links_carry_the_key_after_the_hash(monkeypatch):
    monkeypatch.setattr(share, "lan_addresses", lambda: ["10.1.2.3"])
    monkeypatch.setattr(share.socket, "gethostname", lambda: "ENG-PC-07")
    assert share.links(8765, "k3y") == ["http://ENG-PC-07:8765/#key=k3y", "http://10.1.2.3:8765/#key=k3y"]


def test_lan_addresses_are_never_loopback():
    assert all(not ip.startswith(("127.", "169.254.")) for ip in share.lan_addresses())


def test_sharing_listens_on_the_network_and_requires_the_key(tmp_path, monkeypatch):
    """main() up to the point of serving: network address, key set, browser not opened by the server."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("EKA_")}   # share.main() writes os.environ
    env.update(EKA_CONFIG=str(tmp_path / "none.env"), EKA_DATA_DIR=str(tmp_path / "data"), EKA_SHARE_OPEN_BROWSER="0")
    monkeypatch.setattr(os, "environ", env)
    seen = {}
    import eka.__main__ as entry
    monkeypatch.setattr(entry, "main", lambda: seen.update(host=os.environ["EKA_HOST"], key=os.environ["EKA_API_TOKEN"],
                                                           browser=os.environ["EKA_OPEN_BROWSER"]))
    share.main()
    assert seen["host"] == "0.0.0.0" and seen["key"] and seen["browser"] == "0"
    saved = (tmp_path / "data" / "team-link.txt").read_text()
    assert "#key=" + seen["key"] in saved and "Team key" in saved
