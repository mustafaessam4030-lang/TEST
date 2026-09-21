"""Every text file this project touches is UTF-8; say so explicitly.

Windows defaults read_text()/write_text() to the locale code page. On an
English install that is cp1252, which cannot decode the Arabic in the chat
page — the launcher died before it could bind its port, and the symptom was a
browser saying ERR_CONNECTION_REFUSED with no error anywhere in sight.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCES = sorted(
    p for d in ("scripts", "frontend", "services/automation/app")
    for p in (ROOT / d).rglob("*.py")
    if "artifacts" not in p.parts and ".venv" not in p.parts
)

BARE_READ = re.compile(r"\.read_text\(\s*\)")
BARE_WRITE = re.compile(r"\.write_text\(((?:[^()]|\([^()]*\))*)\)")
BARE_OPEN = re.compile(r"(?<![\w.])open\(((?:[^()]|\([^()]*\))*)\)")


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_text_io_declares_utf8(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    problems: list[str] = []

    for match in BARE_READ.finditer(source):
        problems.append(f"read_text() with no encoding at offset {match.start()}")
    for match in BARE_WRITE.finditer(source):
        if "encoding=" not in match.group(1):
            problems.append(f"write_text({match.group(1)[:40]}…) with no encoding")
    for match in BARE_OPEN.finditer(source):
        args = match.group(1)
        if '"rb"' in args or "'rb'" in args or '"wb"' in args or "'wb'" in args:
            continue                                   # binary is fine
        if "encoding=" not in args:
            problems.append(f"open({args[:40]}…) with no encoding")

    assert not problems, f"{path.relative_to(ROOT)}: " + "; ".join(problems)


def test_the_chat_page_is_unreadable_under_a_windows_code_page() -> None:
    """Guards the specific file that caused the outage: if this ever decodes as
    cp1252, the page has lost its Arabic and something is wrong."""
    raw = (ROOT / "frontend" / "mantrac-support-v9.html").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("cp1252")
    raw.decode("utf-8")                                # must be valid UTF-8
