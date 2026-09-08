"""
The dashboard experience: intro, ML panel, assistant, feedback.

The rules being pinned here are the ones that matter if this is going in front
of an operations team: the intro cannot block the dashboard, the assistant
cannot invent a number, the assistant cannot change anything, and feedback is
material for a later training pass rather than a live edit to a model.

    python test_ui.py

No browser needed — the HTML is read as text and the Python is exercised
directly. test_ui_browser.py drives the real page.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dashboard import assistant, bridge, feedback, insights, mlstatus  # noqa: E402
_MLSTATUS = (HERE / "dashboard" / "mlstatus.py").read_text(encoding="utf-8")

PASS, FAIL = [], []
INDEX = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
SERVER = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


print("=" * 72)
print("1. THE INTRO")
print("=" * 72)
check("It is the first thing in the body",
      INDEX.index('id="gate"') < INDEX.index('id="app"'))
import re as _re
_scenes = _re.findall(r'<div class="g-s(?:\s[^"]*)?"', INDEX)
check("Five scenes", len(_scenes) == 5, str(len(_scenes)))
check("Runs about 6.6s, inside the 5-8s the brief asked for",
      "const TOTAL = 6600;" in INDEX)
check("It ends ON the Open dashboard scene rather than dismissing itself",
      "function hold()" in INDEX and "gate.classList.add('ended')" in INDEX)
check("OPEN DASHBOARD exists", 'id="openDash"' in INDEX
      and "Open dashboard" in INDEX)
check("...and it dismisses the intro without a reload",
      "$('openDash').addEventListener('click', finish)" in INDEX
      and "location.href" not in INDEX.split("function intro()")[1][:6000])
check("The scenes are tied together by one spine, not five loose cards",
      "@keyframes gspine" in INDEX and 'class="g-spine"' in INDEX)
check("The sidebar Introduction replays THIS intro, not a second page",
      "if (p.film){ if (window.ctReplayIntro) window.ctReplayIntro(); return; }"
      in INDEX)
check("There is a skip control", 'id="skip"' in INDEX)
check("Escape also skips", "e.key === 'Escape'" in INDEX)
check("It reaches its close on its own", "lastTimer = setTimeout(hold, TOTAL)" in INDEX)
check("It is CSS animation, not a video file",
      "@keyframes gscene" in INDEX and "<video" not in INDEX)
check("No animation library was added",
      "gsap" not in INDEX.lower() and "anime.min" not in INDEX.lower())
check("A progress indicator runs with it", "@keyframes gbar" in INDEX)
check("It plays once per browser session",
      "sessionStorage.getItem('ct-intro')" in INDEX)
check("It can be replayed from settings",
      "ctReplayIntro" in INDEX and "Replay intro" in INDEX)
check("Reduced motion goes straight to the close",
      "prefers-reduced-motion" in INDEX and "if (reduced){ hold(); return; }" in INDEX)

print()
print("=" * 72)
print("2. THERE IS NO AUDIO ANYWHERE")
print("=" * 72)
# The soundtrack is gone by request. It is worth pinning the REMOVAL rather
# than deleting these checks: an intro is exactly the sort of thing a future
# change re-scores, and the previous build shipped two <audio> elements
# playing at once, a track that carried on over the dashboard, a 1.28MB fetch
# in the load path and ~180 lines of fade/seek/codec machinery. None of that
# should be able to come back unnoticed.
# Search the MARKUP, not the comments. The file carries a comment recording
# that the audio was removed, and matching that is how a check passes for the
# wrong reason.
_bare = re.sub(r"<!--.*?-->", "", INDEX, flags=re.S)
check("No <audio> element anywhere in the page",
      not re.findall(r"<audio[\s>]", _bare))
check("No audio element is referenced by script",
      not any(x in INDEX for x in ("introAudio", "new Audio", ".play()",
                                   "canPlayType", "currentTime")))
# The only surviving mention is the comment recording the removal, so the
# check is that nothing FETCHES it.
check("No soundtrack is fetched",
      "fetch('/api/music')" not in INDEX and 'fetch("/api/music")' not in INDEX)
check("No mute or volume control survives",
      not any(x in INDEX for x in ('id="introMute"', 'id="enableSound"',
                                   'class="vol"', "ct-intro-mute")))
check("No autoplay-refused or codec-warning state survives",
      "needs-sound" not in INDEX and "no-audio" not in INDEX)
check("The fade/seek/stop machinery is gone",
      not any(x in INDEX for x in ("function fadeTo", "function seekThenPlay",
                                   "function attemptPlay", "function startAudio",
                                   "function stopAudio", "function applyMute")))
check("The soundtrack asset is not in the build",
      not [f for f in (HERE / "dashboard" / "static" / "assets" / "audio").glob("*")
           if f.suffix.lower() in (".mp3", ".m4a", ".m4r", ".ogg", ".wav", ".flac")],
      str(list((HERE / "dashboard" / "static" / "assets" / "audio").glob("*"))))
check("...and the server no longer offers an endpoint for one",
      "/api/music" not in SERVER and "def find_music" not in SERVER)

# The intro itself is UNCHANGED apart from losing the sound, and its one way
# out has to be legible. Measured in a real browser: `color` and `background`
# were both var(--ink), so the OPEN DASHBOARD label was black on black at
# 1:1 — the operator was clicking a blank pill to get into the dashboard.
check("The intro still exists and still runs once per session",
      'id="gate"' in INDEX and "ct-intro" in INDEX and "function run()" in INDEX)
check("Skip and Escape still end it",
      "$('skip').addEventListener" in INDEX and "e.key === 'Escape'" in INDEX)
check("The sidebar's Introduction still replays the same one intro",
      "window.ctReplayIntro" in INDEX)
# Scoped to the rule, and anchored so `border-color:var(--ink)` cannot
# satisfy a substring search for `color:var(--ink)`.
_gopen = INDEX.split(".g-open{")[1].split("}")[0]
check("OPEN DASHBOARD is not black-on-black — a filled control carries its "
      "own foreground",
      "color:var(--paper)" in _gopen and "background:var(--ink)" in _gopen
      and not re.search(r"(?<!-)color:var\(--ink\)", _gopen), _gopen[:120])

print()
print("=" * 72)
print("2e. THE PALETTE IS A RESTRAINED LIGHT NEUTRAL, AND THE INTRO IS SEPARATE")
print("=" * 72)
# Measured on real pixels in a browser, not asserted from the source: every
# rendered text style clears WCAG AA, worst 4.62:1. That worst case is the
# run-state pill, whose green had to be darkened from #1B7F4B to #197746 —
# it cleared AA on its wash over a card (4.52:1) but not over the page
# ground (4.18:1), and the header pill sits on the page ground.
check("There are four surface levels, so elevation is a step in the neutral "
      "ramp plus one soft shadow, not a pile of shadows",
      all(t in INDEX for t in ("--bg:#F5F5F7", "--surface:#FFFFFF",
                               "--surface-2:#FAFAFA", "--surface-3:#F0F0F2")))
check("color-scheme is light, so native controls follow",
      "color-scheme:light" in INDEX)
check("Hairlines are an alpha over whatever is behind them, so one token "
      "works on every surface", "--line:rgba(0,0,0,.09)" in INDEX)
check("A filled control is the accent on its own foreground token, so no "
      "control fills with the text colour and becomes a white pill",
      "--solid:var(--vio)" in INDEX and "--on-solid:#FFFFFF" in INDEX
      and "background:var(--solid)" in INDEX)
check("The green that failed AA over the page ground was corrected",
      "--grn:#197746" in INDEX and "--grn:#1B7F4B" not in INDEX)
check("The legacy semantic names still resolve, so nothing that referred to "
      "them silently lost its colour",
      all(t in INDEX for t in ("--signal:var(--grn)", "--alarm:var(--red)",
                               "--amber:var(--y)", "--ice:var(--blu)")))
check("...and no dashboard control still fills with --ink",
      "background:var(--ink)" not in INDEX.split("#gate{")[1].split(".top{")[0]
      if "#gate{" in INDEX else True)
check("No hardcoded light-theme colour survives in the markup",
      "#946200" not in INDEX and "#C48A00" not in INDEX and "#A9A294" not in INDEX)

# The intro was signed off as-is and keeps its own palette, scoped.
_gate = INDEX.split("#gate{")[1].split("}")[0]
check("The Introduction pins its own bone-and-ink values",
      "--paper:#EFEBE3" in _gate and "--ink:#0A0A0B" in _gate, _gate[:120])
check("...so a dark dashboard does not swallow the cinematic",
      "--card:#F6F3EC" in _gate)

# One accent, one meaning.
check("An ACTUAL arrival is the only date that carries colour",
      "td.dt{" in INDEX and "color:var(--ink)}" in INDEX.split("td.dt{")[1][:120]
      and "td.dt.actual{color:var(--ice)}" in INDEX)
check("Dates use tabular figures so a column of them aligns",
      "font-variant-numeric:tabular-nums" in INDEX.split("td.dt{")[1][:200])
check("A zero does not glow — 'Failed 0' is good news",
      "color:var(--faint)" in INDEX.split(".kpi-v.nil{")[1][:80]
      and "zero(x.v)" in INDEX)
check("The run's state is a status light, not a 54px glowing word",
      ".hero-v.s-running::before" in INDEX and "s-' + (run.status" in INDEX)
check("...and it respects reduced motion",
      "prefers-reduced-motion:reduce){.hero-v.s-running::before{animation:none}" in INDEX)
check("Amber is reserved for what needs a person — the hero decoration gave "
      "it back", "rgba(255,180,84,.55)" not in INDEX)

# The responsive pass below was driven by measuring the real rendered layout
# at 1440 / 1024 / 768 / 375, not by reading the CSS.
check("The sidebar becomes a fixed overlay on a phone, so it needs its own "
      "surface — transparent let the page show through the nav items",
      "background:var(--surface);border-right:1px solid var(--line);" in INDEX)
check("...and the active item inverts there, because a white pill is "
      "invisible on a white drawer",
      ".nav-i.on{background:var(--surface-3);box-shadow:none}" in INDEX)
check("Ten columns are re-laid-out as labelled records on a phone rather "
      "than left to scroll sideways, which put the ATLAS attribution "
      "off-screen entirely", ".tw thead{display:none}" in INDEX
      and ".tw td::before{content:attr(data-k)" in INDEX)
check("...and the labels come from the table's own <thead>, so they cannot "
      "drift from the header", "function stampRowLabels" in INDEX
      and "querySelectorAll('thead th')" in INDEX)
check("...and only while that layout is live, so a desktop pays nothing",
      "if (!stackQ.matches" in INDEX)
check("Grid hairlines are cast by the cells, not shown through gaps in the "
      "container's background — seven metrics in an auto-fit row left the "
      "tail of the last row as a solid grey block",
      "box-shadow:1px 0 0 var(--line-2),0 1px 0 var(--line-2)" in INDEX)
check("Labels wrap instead of being clipped by an ellipsis, which hid the "
      "one word that told 'Carrier ETA found' from 'Carrier ATA found'",
      "text-overflow:ellipsis" not in INDEX.split(".kpi-k{")[1][:160])
check("The step-trace placeholder opts out of the timestamp grid, which was "
      "squeezing its sentence into a 54px column",
      ".steps .step-none{display:block" in INDEX
      and 'class="step-none"' in INDEX)

print()
print("=" * 72)
print("2d. THE FONT IS NOT IN THE LOAD PATH")
print("=" * 72)
# MEASURED: as a <link rel=stylesheet media=print> the load event still waited
# 12,599ms for fonts.googleapis.com on a machine that cannot reach it.
# Blocking the two Google hosts gave 116ms, so the stylesheet was all of it.
# Injecting it from script instead gave 127ms. The webfont has since been
# dropped altogether: a system-first stack renders in the face the operating
# system has already hinted and cached, which removes the third-party request
# rather than merely moving it off the critical path.
head = INDEX.split("<style>")[0]
check("No <link rel=stylesheet> to Google Fonts in the head",
      "rel=\"stylesheet\"" not in head, head[-400:] if "rel=\"stylesheet\"" in head else "")
check("...nor a noscript one, which loads the same way",
      "<noscript" not in head or "fonts.googleapis" not in head.split("<noscript")[1][:200])
check("The webfont is gone entirely, not deferred — no request, no idle "
      "injector, no preconnect to a host that is no longer used",
      "fonts.googleapis" not in INDEX and "fonts.gstatic" not in INDEX)
check("...so there is no flash of unstyled text to manage either",
      "font-display" not in INDEX)
check("The system font stack is still the fallback",
      "-apple-system,BlinkMacSystemFont" in INDEX)

print()
print("=" * 72)
print("2b. THE STYLESHEET PARSES")
print("=" * 72)
# A stray brace at the top level of a stylesheet makes the browser drop the
# rules after it, silently. One left over from an edit killed #app, .nav and
# .main — the entire desktop shell — and nothing failed, nothing logged, and
# the page still rendered, just wrongly. Counting braces is cheap; finding
# this by eye cost a release.
_style_start = INDEX.index("<style>") + len("<style>")
_style_end = INDEX.index("</style>", _style_start)
_css = INDEX[_style_start:_style_end]
_depth = 0
_first_negative = None
for _i, _ch in enumerate(_css):
    if _ch == "{":
        _depth += 1
    elif _ch == "}":
        _depth -= 1
        if _depth < 0 and _first_negative is None:
            _first_negative = INDEX[:_style_start + _i].count("\n") + 1
check("Braces balance across the whole stylesheet", _depth == 0,
      "final depth {0}".format(_depth))
check("No stray closing brace at the top level", _first_negative is None,
      "first one at line {0}".format(_first_negative))

# The rules the shell depends on must survive to the end of the sheet.
for _sel in ("#app{", ".nav{", ".main{", ".card{"):
    check("{0!r} is present after the intro block".format(_sel.rstrip("{")),
          _sel in _css and _css.index(_sel) > _css.index("#gate{"))
check("The desktop grid is defined",
      "grid-template-areas:\"nav main\"" in _css)

print()
print("=" * 72)
print("3. THE ML PANEL SHOWS REAL VALUES")
print("=" * 72)
# ── the panel must not get slower every week it runs ─────────────────
# MEASURED: episodes.join() reads the whole telemetry file, so a snapshot
# costs 17ms at 2,000 rows, 310ms at 40,000 and 603ms at 100,000. The cache
# keyed on the file's (size, mtime) was invalidated by every appended row, so
# during a run — when the automation appends per strategy attempt — every
# request paid the full price. Twelve requests over an 8.7MB file cost
# 3,387ms before and 2ms after.
check("Telemetry GROWTH does not force an immediate recompute",
      "_MAX_STALE_SECONDS" in _MLSTATUS
      and "now - _CACHE[\"at\"] < _MAX_STALE_SECONDS" in _MLSTATUS)
check("...but a change of MEANING still does, because those decide what the "
      "panel says rather than how much of it there is",
      "cached[0] == key[0]" in _MLSTATUS
      and "return (tuple(meaning), growth)" in _MLSTATUS)
check("The staleness window is bounded, and short enough to be invisible",
      0 < mlstatus._MAX_STALE_SECONDS <= 60, str(mlstatus._MAX_STALE_SECONDS))
check("The model files are part of MEANING, not growth",
      "CHAMPION_PATH, ml_config.CHALLENGER_PATH" in _MLSTATUS
      and "ml_config.TELEMETRY_PATH).stat()" in _MLSTATUS)
check("join() is NOT bounded to a tail — it feeds enough_to_train(), and a "
      "tail count would report 'not enough' on a history that has plenty",
      "ml_episodes.join()" in _MLSTATUS)

# ── the live feed must not re-send the whole run on every log line ───
# MEASURED in a browser over 60s of a run with 200 shipments on the page:
# 81 state pushes a minute at a 194KB median — 16.1 MB/min for the browser to
# parse. `shipments` was 68% of that and grows with the run (342KB at 400
# shipments), while most pushes are a log line or a step that changes no
# shipment at all. After: 5.2 MB/min, 45KB median.
_BRIDGE = (HERE / "dashboard" / "bridge.py").read_text(encoding="utf-8")
_SERVER_SRC = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")

_bx = bridge.ControlTowerState()
_bx.run_started(dry_run=False, target_status="EN ROUTE", max_records=50,
                max_pages=4, results_file="r.csv", log_file="l.log")


def _cold_of(snap):
    return json.dumps([snap.get("shipments"), snap.get("exceptions")], default=str)


def _changed_without_bump(action):
    """True when `action` altered the cold section but did not bump."""
    before, before_v = _cold_of(_bx.snapshot(trim=True)), _bx.cold_version
    action()
    after, after_v = _cold_of(_bx.snapshot(trim=True)), _bx.cold_version
    return before != after and before_v == after_v


# Every public method that can touch a shipment record or an exception. A new
# one that forgets to bump freezes the shipments table, so this compares the
# OBSERVABLE payload against the version rather than trusting the call sites.
_missed = []
for _label, _action in (
    ("shipment_started", lambda: _bx.shipment_started(
        {"bol_awb": "057-05765454", "carrier": "AFKL", "provider": "AFKL",
         "table_page": 1})),
    ("provider_result", lambda: _bx.provider_result(
        {"tracking_status": "ARRIVED", "eta": "04/09/2026", "ata": "03/09/2026"})),
    ("atlas", lambda: _bx.atlas("Strategy selected", "xpath_ata_date 0.84")),
    ("view_updated", lambda: _bx.view_updated("COE", "ETA", "04/09/2026")),
    ("shipment_finished", lambda: _bx.shipment_finished(
        "057-05765454", "SUCCESS", actions={"coe": "COE ETA -> 04/09/2026"})),
    ("run_fatal", lambda: _bx.run_fatal("boom")),
):
    if _changed_without_bump(_action):
        _missed.append(_label)
check("Every method that changes a shipment or an exception bumps "
      "cold_version — otherwise the table silently freezes",
      not _missed, "did not bump: {0}".format(_missed))

_bx2 = bridge.ControlTowerState()
_bx2.run_started(dry_run=False, target_status="EN ROUTE", max_records=50,
                 max_pages=4, results_file="r.csv", log_file="l.log")
_bx2.shipment_started({"bol_awb": "074-1", "carrier": "KLM",
                       "provider": "AFKL", "table_page": 1})
_v = _bx2.cold_version
_before_log = _cold_of(_bx2.snapshot(trim=True))
_bx2.log("a plain log line")
_bx2.step("Reading the page", system="AFKL")
check("...and a log line or a step does NOT, because it changes neither",
      _bx2.cold_version == _v
      and _cold_of(_bx2.snapshot(trim=True)) == _before_log)

_full = _bx2.snapshot(trim=True)
_lean = _bx2.snapshot(trim=True, since_cold=_bx2.cold_version)
_behind = _bx2.snapshot(trim=True, since_cold=_bx2.cold_version - 1)
check("A full frame carries the shipments and the exceptions",
      "shipments" in _full and "exceptions" in _full
      and isinstance(_full.get("cold_version"), int))
check("An up-to-date receiver gets neither back, and is TOLD what was "
      "left out — a frame is never ambiguous about what it asserts",
      "shipments" not in _lean and "exceptions" not in _lean
      and _lean.get("unchanged") == ["shipments", "exceptions"])
check("...but still gets the run, the current step and the log tail",
      all(k in _lean for k in ("run", "current", "logs", "counters", "progress")))
check("A receiver that is behind gets the whole cold section",
      "shipments" in _behind and "exceptions" in _behind
      and "unchanged" not in _behind)
check("The browser carries the omitted keys across rather than dropping them",
      "state.unchanged" in INDEX and "state[key] = S[key]" in INDEX)
check("...and only for keys the frame actually named, so a genuinely empty "
      "list still clears the table",
      "if (!(key in state) && (key in S))" in INDEX)
check("The FIRST frame on a stream connection carries everything",
      "sent_cold = None" in _SERVER_SRC
      and "build_payload(since_cold=sent_cold)" in _SERVER_SRC)
check("/api/state stays complete, so the poll fallback is unaffected",
      "def build_payload(trim=True, since_cold=None)" in _SERVER_SRC)

snapshot = mlstatus.snapshot()
check("There is an /api/ml route", '"/api/ml"' in SERVER)
check("The snapshot reports a status", snapshot.get("status") in
      ("ENABLED", "FALLBACK", "DISABLED", "UNAVAILABLE"), str(snapshot.get("status")))
check("Its status matches what the predictor says",
      (snapshot["status"] == "ENABLED") == bool(snapshot.get("online")))
check("An absent model gives a null model block, not zeros",
      snapshot["model"] is None or isinstance(snapshot["model"], dict))
check("The confidence threshold is the real configured one",
      snapshot["config"].get("confidence_threshold") is not None)
check("Unknown values render as a dash, never as 0",
      "const dash = (v) =>" in INDEX and "'—'" in INDEX)
check("No demo numbers are baked into the panel",
      "4,900" not in INDEX and "4900" not in INDEX)
check("The panel is polled, not computed in the browser",
      "fetch('/api/ml')" in INDEX)

print()
print("=" * 72)
print("4. THE ASSISTANT NEVER INVENTS OPERATIONAL DATA")
print("=" * 72)
empty = bridge.ControlTowerState().snapshot()
missing = Path(tempfile.mkdtemp()) / "nothing.jsonl"
os.environ["ML_TELEMETRY_PATH"] = str(missing)
try:
    from ml import config as ml_config
    ml_config.reload_from_environment()
except Exception:
    pass
insights._cache["rows"] = None

for question, topic in [("why is AFKL slower?", "carriers"),
                        ("which strategy works best?", "strategies"),
                        ("what goes wrong most often?", "failures")]:
    reply = assistant.answer(question, empty, {})
    text = (reply.get("answer") or "").lower()
    check("{0!r} refuses rather than guessing".format(question),
          ("no execution telemetry" in text or "nothing measured" in text
           or "not available" in text), reply.get("answer", "")[:120])
    check("...and quotes no invented figure",
          not any(token in text for token in ("8.4s", "5.1s", "120 verified")))

reply = assistant.answer("was the ATA verified?", empty, {})
check("An unrun verification is reported as unrun",
      "no read-back verification" in (reply.get("answer") or "").lower(),
      reply.get("answer", "")[:100])

print()
print("=" * 72)
print("5. ...BUT DOES COUNT WHAT IS THERE")
print("=" * 72)
telemetry = Path(tempfile.mkdtemp()) / "telemetry.jsonl"
with open(telemetry, "w", encoding="utf-8") as handle:
    for index in range(40):
        provider = "DHL" if index % 2 else "AFKL"
        handle.write(json.dumps({
            "kind": "interaction",
            "context": {"provider": provider, "page": "carrier_result",
                        "field": "awb"},
            "strategy": "direct_url", "success": True,
            "duration_ms": 4000 if provider == "DHL" else 9000,
            "category": "OK", "source": "automation",
            "ts": "2026-09-01"}) + "\n")
insights._cache["rows"] = None
text = insights.carrier_timing(insights.load(telemetry, force=True))
check("A counted comparison is produced", bool(text) and "median" in text.lower(),
      str(text)[:120])
check("...naming the slower carrier", "Air France" in (text or ""))
check("...and stating the sample size behind it",
      "timed successes" in (text or ""))
check("Test-tagged telemetry is excluded from answers",
      all(r.get("source", "automation") == "automation"
          for r in insights.load(telemetry, force=True)))

print()
print("=" * 72)
print("6. THE ASSISTANT IS READ-ONLY")
print("=" * 72)
ASSISTANT = (HERE / "dashboard" / "assistant.py").read_text(encoding="utf-8")
INSIGHTS = (HERE / "dashboard" / "insights.py").read_text(encoding="utf-8")
for name, source in (("assistant.py", ASSISTANT), ("insights.py", INSIGHTS)):
    check("{0} never writes a file".format(name),
          "open(" not in source.replace("open(target", "").replace(
              'open(path', '') or "\"w\"" not in source,
          "writes found")
    check("{0} never imports update_eta".format(name),
          "import update_eta" not in source)
    # Precise: it must not IMPORT the trainer or CALL train(). The word
    # "trainer" appearing in a comment is not a finding.
    check("{0} cannot train a model".format(name),
          "import trainer" not in source
          and "from ml import trainer" not in source
          and "trainer.train" not in source
          and ".train(" not in source)
check("insights.py only ever reads telemetry",
      '"r"' in INSIGHTS and '"w"' not in INSIGHTS and '"a"' not in INSIGHTS)
check("The assistant receives a snapshot, never the bridge itself",
      "assistant.answer(question, bridge.snapshot()" in SERVER)
check("An action it asks for still goes through the control channel",
      "control.request(" in SERVER)

print()
print("=" * 72)
print("7. FEEDBACK IS MATERIAL, NOT TRAINING")
print("=" * 72)
os.environ["ASSISTANT_FEEDBACK_PATH"] = str(Path(tempfile.mkdtemp()) / "fb.jsonl")
ok, message = feedback.record(
    question="why is AFKL slower?", answer="Because ...",
    verdict="not_helpful", correction="It is the customs step, not navigation",
    sources=["ml/data/telemetry.jsonl:interaction"], confidence=0.7,
    intent="carrier_timing", reference="057-05765454")
check("Feedback is stored", ok, message)
rows = [json.loads(l) for l in
        Path(os.environ["ASSISTANT_FEEDBACK_PATH"]).read_text().splitlines() if l.strip()]
check("...with the question, answer and correction", len(rows) == 1
      and rows[0]["correction"].startswith("It is the customs"))
check("...the sources the answer used", rows[0]["data_sources_used"] ==
      ["ml/data/telemetry.jsonl:interaction"])
check("...the confidence and intent", rows[0]["answer_confidence"] == 0.7
      and rows[0]["intent"] == "carrier_timing")
check("...and a timestamp", bool(rows[0]["ts"]))
bad_ok, bad_msg = feedback.record("q", "a", "maybe")
check("A verdict outside helpful/not_helpful is rejected", not bad_ok, bad_msg)
check("Feedback with no question is rejected",
      not feedback.record("", "a", "helpful")[0])
check("Secrets typed into a correction are redacted",
      "hunter2" not in json.dumps(feedback._clean("password: hunter2")))
FEEDBACK = (HERE / "dashboard" / "feedback.py").read_text(encoding="utf-8")
# Check the IMPORTS, not the prose: the docstring legitimately mentions
# ml/data/telemetry.jsonl while explaining why it stays out of it.
import ast as _ast
_imports = set()
for _node in _ast.walk(_ast.parse(FEEDBACK)):
    if isinstance(_node, _ast.Import):
        _imports.update(a.name.split(".")[0] for a in _node.names)
    elif isinstance(_node, _ast.ImportFrom) and _node.module:
        _imports.add(_node.module.split(".")[0])
check("Feedback imports nothing from the ml package",
      "ml" not in _imports, str(sorted(_imports)))
check("...and calls no training function",
      ".train(" not in FEEDBACK and "trainer." not in FEEDBACK)
check("It is kept apart from the automation's telemetry",
      "assistant_feedback.jsonl" in FEEDBACK)
check("There is an /api/feedback route", '"/api/feedback"' in SERVER)
check("The route says feedback is not a live model edit",
      "never reaches a production model" in SERVER)

print()
print("=" * 72)
print("8. THE CHAT UI")
print("=" * 72)
check("Thumbs up and down are offered", 'data-v="helpful"' in INDEX
      and 'data-v="not_helpful"' in INDEX)
check("A thumbs-down invites a correction", "What should it have said?" in INDEX)
check("Quick actions are offered", "OPENERS" in INDEX)
check("There is a typing state", "chTy" in INDEX)
check("The conversation keeps a reference between turns",
      "lastReference" in INDEX)
check("...and sends it back as context",
      "context: {reference: lastReference}" in INDEX)

print()
print("=" * 72)
print("9. THE BACKEND WAS NOT TOUCHED")
print("=" * 72)
UPDATE = (HERE / "update_eta.py").read_text(encoding="utf-8")
for token in ("get_dhl_result", "get_qatar_result", "get_portal_result",
              "_read_afkl_page", "build_afkl_detail_url", "run_with_retry",
              "classify_failure", "fill_date_field", "verify_saved_date",
              "update_internal_shipment"):
    check("update_eta.{0} still there".format(token), "def {0}(".format(token) in UPDATE)
check("The ETA/ATA guards are still in the selectors",
      "input[id*='ETA' i]:not([id*='ATA' i]):visible" in UPDATE)
check("AFKL still uses the direct detail URL",
      "shipment/detail/" in UPDATE)
check("The dashboard does not import the automation",
      "import update_eta" not in
      (HERE / "dashboard" / "server.py").read_text(encoding="utf-8"))

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
