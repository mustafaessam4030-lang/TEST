"""
The pre-launch page (/launch): when ATLAS opens, and the soundtrack, if the
company has one it may play.

Configured by the server's administrator through machine environment
variables (read when the tower starts), never by the page:

    ATA_LAUNCH_AT       the launch moment, ISO 8601 WITH its UTC offset,
                        e.g. 2026-11-01T09:00:00+02:00. Without an offset the
                        moment is ambiguous, so it is refused.
    ATA_LAUNCH_TZ       the IANA time zone the page shows it in, e.g.
                        Africa/Cairo (default UTC). When it is set and Python
                        knows the zone, the offset above must match it at
                        that moment.
    ATA_LAUNCH_AUDIO    a file the company is licensed to host, put in
                        dashboard/static/launch/audio/ (file name only), or
                        an https:// address of a licensed stream.
    ATA_LAUNCH_AUDIO_TITLE / ATA_LAUNCH_AUDIO_ARTIST   what the player shows.

The page is public and decides nothing: sign-in, roles and every protected
route stay exactly as they are, before and after the launch moment. All a
pending launch changes is where a signed-out visitor to "/" is sent.
"""
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
LAUNCH_DIR = HERE / "static" / "launch"
AUDIO_DIR = LAUNCH_DIR / "audio"
AUDIO_SUFFIXES = (".mp3", ".m4a", ".ogg", ".oga", ".opus", ".webm", ".wav", ".flac")
# What /static/launch/ will serve at all; anything else there is refused.
# The page itself is served only at /launch, with its security headers.
PAGE_FILES = ("launch.css", "launch.js")

_TZ_NAME = re.compile(r"^(UTC|[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){1,2})$")
_AUDIO_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-]{0,120}$")

DEFAULT_TITLE = "Lilith"
DEFAULT_ARTIST = "Saint Avangeline"


def parse_moment(raw):
    """(aware datetime in UTC, None) or (None, why)."""
    raw = (raw or "").strip()
    if not raw:
        return None, "not set"
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None, "not an ISO 8601 date and time"
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None, "has no UTC offset (write it like 2026-11-01T09:00:00+02:00)"
    return moment.astimezone(timezone.utc), None


def check_zone(name, moment_utc, raw):
    """None when the zone is usable for showing this moment, else why not."""
    if not _TZ_NAME.match(name):
        return "is not an IANA time zone name"
    try:
        from zoneinfo import ZoneInfo, available_timezones
        zone = ZoneInfo(name)
    except Exception:
        # Windows Python has no zone database without the tzdata package. The
        # moment itself is exact (it carries its offset); the browser, which
        # always has the database, names the zone. Where there IS a database,
        # a name missing from it is a typo, not a gap.
        try:
            if available_timezones():
                return "is not a known IANA time zone"
        except Exception:
            pass
        return None
    text = raw.strip()
    text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    given = datetime.fromisoformat(text).utcoffset()
    actual = moment_utc.astimezone(zone).utcoffset()
    if given != actual:
        return "says UTC{0} but {1} is UTC{2} at that moment".format(
            _fmt_offset(given), name, _fmt_offset(actual))
    return None


def _fmt_offset(delta):
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return "{0}{1:02d}:{2:02d}".format(sign, minutes // 60, minutes % 60)


def remaining(launch_utc, now_utc):
    """Whole days, hours, minutes and seconds until launch; zeros once it has passed."""
    # Rounded up: in the last second before launch it still says 1, never 0.
    total = math.ceil((launch_utc - now_utc).total_seconds())
    total = max(0, total)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    return {"total_seconds": total, "days": days, "hours": hours,
            "minutes": minutes, "seconds": seconds}


def audio_source(env=None):
    """(url, None) for a licensed source that is really there, or (None, why)."""
    env = os.environ if env is None else env
    raw = (env.get("ATA_LAUNCH_AUDIO") or "").strip()
    if not raw:
        return None, "no licensed soundtrack is configured"
    if raw.lower().startswith("https://"):
        from urllib.parse import urlsplit
        try:
            parts = urlsplit(raw)
        except ValueError:
            parts = None
        if parts is None or re.search(r"[\s\"'<>\\]", raw) or not parts.hostname \
                or parts.username or parts.password:
            return None, "the soundtrack address is not valid (and must not carry a password)"
        return raw, None
    if "/" in raw or "\\" in raw or not _AUDIO_FILE.match(raw) or \
            not raw.lower().endswith(AUDIO_SUFFIXES):
        return None, "ATA_LAUNCH_AUDIO must be a file name in dashboard/static/launch/audio/ or an https:// address"
    path = AUDIO_DIR / raw
    if not path.is_file():
        return None, "the soundtrack file is not on this server"
    return "/static/launch/audio/" + quote(raw), None


def config(env=None, now=None):
    """Everything the page needs, and nothing more. Safe to send to anyone."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    raw = env.get("ATA_LAUNCH_AT") or ""
    zone_given = (env.get("ATA_LAUNCH_TZ") or "").strip()
    zone = zone_given or "UTC"
    moment, why = parse_moment(raw)
    if moment is None:
        why = "ATA_LAUNCH_AT " + why if raw.strip() else why
    elif zone_given:
        why = check_zone(zone, moment, raw)
        if why:
            why = "ATA_LAUNCH_TZ " + why
    if moment is None or why:
        state = "unset" if not raw.strip() else "invalid"
        launch_iso = None
    else:
        state = "launched" if now >= moment else "countdown"
        launch_iso = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    audio, audio_why = audio_source(env)
    return {
        "state": state,
        # Why the date is not in use, for whoever configures it; it names
        # settings, never their values beyond the date itself.
        "problem": why if state == "invalid" else None,
        "launch_at": launch_iso,
        "launch_ms": int(moment.timestamp() * 1000) if launch_iso else None,
        "timezone": zone,
        "server_now_ms": int(now.timestamp() * 1000),
        "remaining": remaining(moment, now) if launch_iso else None,
        "audio": {
            "url": audio,
            "title": (env.get("ATA_LAUNCH_AUDIO_TITLE") or DEFAULT_TITLE).strip()[:80],
            "artist": (env.get("ATA_LAUNCH_AUDIO_ARTIST") or DEFAULT_ARTIST).strip()[:80],
            "unavailable": audio_why,
        },
        "login": "/login",
    }


def pending(env=None, now=None):
    """True while a valid launch moment is still ahead."""
    return config(env, now)["state"] == "countdown"


def asset(route):
    """
    The file under /static/launch/ this route names, or None. Decided on the
    name alone, before the filesystem is touched: on Windows a path such as
    \\\\host\\share would otherwise make the server connect out to that host.
    """
    from urllib.parse import unquote
    name = unquote(route[len("/static/launch/"):])
    if name in PAGE_FILES:
        return LAUNCH_DIR / name
    if name.startswith("audio/"):
        file = name[len("audio/"):]
        if _AUDIO_FILE.match(file) and file.lower().endswith(AUDIO_SUFFIXES) and ".." not in file:
            path = AUDIO_DIR / file
            return path if path.is_file() else None
    return None
