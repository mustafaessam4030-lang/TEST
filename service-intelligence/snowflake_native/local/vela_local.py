"""VELA AI on your computer: the same Service Intelligence app, with a local Ollama model as the brain and the
Snowflake AI Brain (fleet data, training library, repair history, videos, rules) as the knowledge.

    streamlit run local/vela_local.py          (or start_vela_local.bat / start_vela_local.sh)

What runs where
- Ollama (this computer): understanding the question, planning, writing the answer, reading photos (vision model).
- Snowflake (read-only role): every data query, Cortex Search over the training library / videos / repairs, and
  Cortex web search. Credentials come from ~/.snowflake/connections.toml (key-pair), never from this code.
- Nothing is written to Snowflake: plans and chat logs need the app's owner role, so they are skipped here.

Settings (environment variables, defaults in brackets)
  VELA_SNOWFLAKE_CONNECTION  [vela]          name of the connection in connections.toml
  VELA_LLM                   [ollama]        ollama | cortex  (cortex = same as the Snowflake app, for comparison)
  VELA_OLLAMA_MODEL          [qwen2.5:14b]   chat model
  VELA_VISION_MODEL          [qwen2.5vl:7b]  photo model; empty = photos are not read
  OLLAMA_HOST                [http://localhost:11434]
  VELA_NUM_CTX               [16384]         context window given to Ollama
  VELA_USER_EMAIL            [your Snowflake user]
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                           # snowflake_native/
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from ollama_client import Ollama, OllamaError, wants_json  # noqa: E402

CONNECTION = os.environ.get("VELA_SNOWFLAKE_CONNECTION", "vela")
LLM = os.environ.get("VELA_LLM", "ollama").strip().lower()
CHAT_MODEL = os.environ.get("VELA_OLLAMA_MODEL", "qwen2.5:14b")
VISION_MODEL = os.environ.get("VELA_VISION_MODEL", "qwen2.5vl:7b").strip()
OLLAMA = Ollama(os.environ.get("OLLAMA_HOST", "http://localhost:11434"), int(os.environ.get("VELA_NUM_CTX", "16384")))

@st.cache_resource(show_spinner="Connecting to Snowflake…")
def snowflake_session():
    from snowflake.snowpark import Session
    return Session.builder.config("connection_name", CONNECTION).create()


@st.cache_resource(show_spinner=False)
def app_source() -> str:
    """The same single-file app that runs in Snowflake, built from the current sources."""
    import build_single_file
    return build_single_file.build()


@st.cache_resource(show_spinner=False)
def local_photos() -> dict:
    return {}                                                # photo id -> JPEG bytes (kept on this computer)


def ollama_complete(session, messages: list[dict], max_tokens: int = 1500) -> str:
    return OLLAMA.chat(CHAT_MODEL, messages, max_tokens, json_mode=wants_json(messages))


def save_photo_locally(session, data: bytes, user: str, chat_id: str, serial=None) -> str:
    pid = f"local/{uuid.uuid4()}.jpg"
    local_photos()[pid] = data
    return pid


def look_locally(session, paths: list, question: str, models=()) -> str:
    if not VISION_MODEL:
        raise RuntimeError("no local vision model configured (set VELA_VISION_MODEL, e.g. qwen2.5vl:7b)")
    brain = sys.modules["si_native.brain"]
    images = [local_photos()[p] for p in paths[:brain.MAX_PHOTOS] if p in local_photos()]
    labels = " ".join(f"Photo {i + 1} is attached." for i in range(len(images)))
    prompt = brain.VISION_PROMPT.format(q=str(question)[:600].replace("{", "(").replace("}", ")"), photos=labels)
    return OLLAMA.chat(VISION_MODEL, [{"role": "user", "content": prompt}], 700, images=images)


def preflight() -> None:
    """Clear messages instead of stack traces when Ollama or the Snowflake connection is not ready."""
    if LLM == "ollama":
        try:
            have = OLLAMA.models()
        except OllamaError as exc:
            st.error(f"{exc}\n\nInstall Ollama from ollama.com, start it, then reload this page.")
            st.stop()
        missing = [m for m in (CHAT_MODEL, VISION_MODEL) if m and not any(h == m or h == f"{m}:latest" for h in have)]
        if CHAT_MODEL in missing:
            st.error(f"The model **{CHAT_MODEL}** is not downloaded yet. In a terminal run:\n\n`ollama pull {CHAT_MODEL}`")
            st.stop()
        if missing:
            st.toast(f"Photo model {VISION_MODEL} not found (ollama pull {VISION_MODEL}); photos will not be read.")
    try:
        snowflake_session()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not connect to Snowflake with connection **{CONNECTION}** from connections.toml.\n\n{str(exc)[:400]}")
        st.stop()


preflight()
g = {"__name__": "vela_local"}
exec(compile(app_source(), "streamlit_app.py", "exec"), g)  # installs the si_native modules

ui, agent, brain = (sys.modules[f"si_native.{m}"] for m in ("ui", "agent", "brain"))
ui.session = snowflake_session
_email = os.environ.get("VELA_USER_EMAIL")
ui.user_email = lambda: (_email or str(snowflake_session().get_current_user() or "local").strip('"')).lower()
if LLM == "ollama":
    agent.cortex_complete = ollama_complete
    agent.MODEL = f"{CHAT_MODEL} · Ollama"
    brain.save_photo = save_photo_locally                    # photos stay on this computer (read-only role)
    brain.look = look_locally
g["render"]()
