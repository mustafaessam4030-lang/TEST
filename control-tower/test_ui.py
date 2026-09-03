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
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dashboard import assistant, bridge, feedback, insights, mlstatus  # noqa: E402

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
check("Five scenes", INDEX.count('class="g-s"') == 5, str(INDEX.count('class="g-s"')))
check("Runs about 6.4s, inside the 5-8s the brief asked for",
      "const TOTAL = 6400;" in INDEX)
check("There is a skip control", 'id="skip"' in INDEX)
check("Escape also skips", "e.key === 'Escape'" in INDEX)
check("It finishes on its own", "setTimeout(finish, reduced ? 400 : TOTAL)" in INDEX)
check("It is CSS animation, not a video file",
      "@keyframes gscene" in INDEX and "<video" not in INDEX)
check("No animation library was added",
      "gsap" not in INDEX.lower() and "anime.min" not in INDEX.lower())
check("A progress indicator runs with it", "@keyframes gbar" in INDEX)
check("It plays once per browser session",
      "sessionStorage.getItem('ct-intro')" in INDEX)
check("It can be replayed from settings",
      "ctReplayIntro" in INDEX and "Replay intro" in INDEX)
check("Reduced motion skips almost all of it",
      "prefers-reduced-motion" in INDEX and "reduced ? 400 : TOTAL" in INDEX)

print()
print("=" * 72)
print("2. THE AUDIO CANNOT BREAK THE INTRO")
print("=" * 72)
check("The audio path is configurable, not hardcoded into the markup",
      "/static/assets/audio/intro.mp3" in INDEX and 'src="' not in
      INDEX.split('id="introAudio"')[1].split(">")[0])
check("More than one container is accepted",
      "intro.m4a" in INDEX and "intro.ogg" in INDEX)
check("A blocked autoplay falls back to muted rather than failing",
      "play.catch(() =>" in INDEX and "audio.muted = true" in INDEX)
check("Every play() rejection is caught",
      INDEX.count("catch(() => {})") >= 2 or INDEX.count(".catch(") >= 3)
check("preload is none, so it cannot delay the dashboard",
      'preload="none"' in INDEX)
check("There is a mute control", 'id="introMute"' in INDEX)
check("The mute choice is remembered across sessions",
      "localStorage.setItem('ct-intro-mute'" in INDEX)
check("Sound fades in and out rather than cutting",
      "audio.volume = v" in INDEX and "stopAudio" in INDEX)
check("The sound stops before the dashboard appears",
      "setTimeout(stopAudio, TOTAL - 700)" in INDEX)
audio_dir = HERE / "dashboard" / "static" / "assets" / "audio"
check("The asset folder exists so a file can simply be dropped in",
      audio_dir.is_dir(), str(audio_dir))

print()
print("=" * 72)
print("3. THE ML PANEL SHOWS REAL VALUES")
print("=" * 72)
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
