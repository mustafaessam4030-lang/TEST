"""The reasoning layer: understand, decide, and refuse to answer unsafely.

This sits between the user and the deterministic tools. It never talks to a
browser, never holds a credential and never writes to a store; it decides
*what should happen* and hands that decision to machinery that already exists.

The order is deliberate and is the whole design:

    understand → identify → validate → resolve context → plan → (tools) → verify

Nothing downstream is asked to run until the identifier has survived every step
above it, and nothing is said to the user until `verify` has confirmed that the
words about to be spoken are backed by data a tool actually returned.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.agent import intent as intents
from app.agent import resolve
from app.agent.entities import extract_serials, format_valid, normalize_serial
from app.agent.state import (
    AgentState, ConversationContext, Intent, NEEDS_SERIAL, ResponseMode, ToolCall,
)
from app.core.logging import log

logger = logging.getLogger(__name__)

#: Fields the store can answer without touching the source, if it holds the record.
STORE_ANSWERABLE = frozenset({
    "machine_serial_number", "machine_build_date", "engine_serial_number",
    "engine_build_date", "equipment_model", "equipment_type", "engine_family",
    "build_date", "parts_data", "specifications", "parts_manual_url", "serial_number",
})

#: The user asking for current data, not whatever we hold.
_WANTS_FRESH = re.compile(
    r"\b(refresh|re-?check|latest|newest|current|again|update|from sis|live)\b"
    r"|حدّث|حدث|من sis|أحدث|اخر", re.I)

_AFFIRMATIVE = {"yes", "y", "yeah", "yep", "correct", "right", "ok", "okay", "sure",
                "please", "do it", "go", "أيوة", "ايوه", "نعم", "تمام", "صح"}
_NEGATIVE = {"no", "n", "nope", "wrong", "incorrect", "لا", "غلط", "مش ده"}


class MaiaBrain:
    """Stateless per call; conversation state is passed in and handed back.

    Keeping it stateless means the browser chat, the Python agent and the test
    suite all exercise exactly the same logic with no hidden session.
    """

    def __init__(self, repo: Any = None) -> None:
        self.repo = repo

    # ── the turn ────────────────────────────────────────────────────────────
    async def understand(self, utterance: str,
                         context: ConversationContext | None = None) -> AgentState:
        context = context.model_copy(deep=True) if context else ConversationContext()
        state = AgentState(utterance=utterance or "",
                           language=intents.detect_language(utterance),
                           context=context)

        state.extracted = extract_serials(state.utterance)
        awaiting = context.awaiting
        state.intent, state.intent_confidence = intents.classify(
            state.utterance, has_serial=bool(state.extracted),
            has_context=bool(context.active_serial), awaiting=awaiting)
        state.requested_fields = intents.fields_for(
            state.intent, intents.requested_fields(state.utterance))

        if awaiting:
            handled = self._answer_to_our_question(state, awaiting)
            if handled:
                return state
            # They did not answer our question — they asked something else. Read
            # the turn on its own terms, or every later reply stays trapped as a
            # "clarification" of a question nobody is answering any more.
            state.intent, state.intent_confidence = intents.classify(
                state.utterance, has_serial=bool(state.extracted),
                has_context=bool(context.active_serial), awaiting=None)
            state.requested_fields = intents.fields_for(
                state.intent, intents.requested_fields(state.utterance))
            state.notes.append("the reply did not answer our question; read as a new request")

        await self._identify(state)
        if state.response_mode not in (ResponseMode.ASK_SERIAL,):
            pass
        self._finalize(state)
        return state

    # ── replying to a question we asked ─────────────────────────────────────
    def _answer_to_our_question(self, state: AgentState, awaiting: str) -> bool:
        """A turn that answers our own question is read as that answer first.

        "yes" after "did you mean JAZ01865?" is a confirmation, not a new
        request — and reading it any other way is how a conversation loops.
        """
        text = state.utterance.strip().lower()
        context = state.context
        pending = context.pending_candidates

        # An explicit serial always wins: the user corrected themselves.
        if state.extracted:
            context.awaiting = None
            context.pending_candidates = []
            state.notes.append("the reply carried a serial, so it is treated as a new request")
            return False

        if awaiting == "confirmation" and pending:
            if any(word == text or text.startswith(word + " ") or text == word
                   for word in _AFFIRMATIVE):
                chosen = pending[0].serial_number
                state.serial_number = chosen
                state.serial_source = "candidate"
                context.remember(chosen, confirmed=True)
                context.awaiting = None
                context.pending_candidates = []
                state.intent = context.last_intent or Intent.EQUIPMENT_LOOKUP
                state.notes.append(f"user confirmed the suggested serial {chosen}")
                state.validation.intent_understood = True
                state.validation.serial_identified = True
                state.validation.serial_format_valid = True
                state.response_mode = ResponseMode.RUN_LOOKUP
                state.tool_plan = self._plan(state, exists_in_store=None)
                state.message = f"Checking {chosen}."
                return True
            if text in _NEGATIVE:
                context.awaiting = "serial"
                context.pending_candidates = []
                state.response_mode = ResponseMode.ASK_SERIAL
                state.message = ("No problem — please send the serial number as it appears "
                                 "on the machine.")
                state.validation.needs_clarification = True
                return True

        if awaiting == "candidate_choice" and pending:
            picked = self._match_choice(text, pending)
            if picked:
                state.serial_number = picked
                state.serial_source = "candidate"
                context.remember(picked, confirmed=True)
                context.awaiting = None
                context.pending_candidates = []
                state.intent = context.last_intent or Intent.EQUIPMENT_LOOKUP
                state.validation.intent_understood = True
                state.validation.serial_identified = True
                state.validation.serial_format_valid = True
                state.response_mode = ResponseMode.RUN_LOOKUP
                state.tool_plan = self._plan(state, exists_in_store=None)
                state.message = f"Checking {picked}."
                state.notes.append(f"user chose {picked} from the offered candidates")
                return True

        return False

    @staticmethod
    def _match_choice(text: str, pending: list[Any]) -> str | None:
        """Pick by serial, by its distinctive tail, or by position in the list."""
        cleaned = normalize_serial(text)
        for candidate in pending:
            if cleaned == candidate.serial_number:
                return candidate.serial_number
        for candidate in pending:
            tail = candidate.serial_number[-4:]
            if cleaned and cleaned == tail:
                return candidate.serial_number
        ordinals = {"first": 0, "1": 0, "one": 0, "second": 1, "2": 1, "two": 1,
                    "third": 2, "3": 2, "last": len(pending) - 1}
        index = ordinals.get(text.strip())
        if index is not None and 0 <= index < len(pending):
            return pending[index].serial_number
        return None

    # ── identifying the machine ─────────────────────────────────────────────
    async def _identify(self, state: AgentState) -> None:
        validation = state.validation
        validation.intent_understood = state.intent not in (Intent.UNKNOWN,)
        context = state.context

        if state.intent in (Intent.HELP,):
            state.response_mode = ResponseMode.HELP
            return
        if state.intent is Intent.CREDENTIALS:
            # Maia never holds a credential, so there is nothing to reveal —
            # but the answer is a plain no, not a request for a serial number.
            state.response_mode = ResponseMode.REFUSE
            validation.intent_understood = True
            state.message = ("لا أقدر أشارك بيانات تسجيل الدخول أو أي كلمات سر. "
                             "أقدر أساعدك في بيانات أي معدة برقم السيريال."
                             if state.language == "ar" else
                             "I can't share sign-in details, passwords or tokens — I never "
                             "have access to them. I can look up any machine by its serial.")
            return
        if state.intent is Intent.SMALL_TALK:
            validation.intent_understood = True
            state.response_mode = ResponseMode.CHAT
            state.message, state.suggestions = small_talk_reply(
                state.utterance, context.active_serial if context.confirmed or
                not context.pending_candidates else None, state.language)
            return
        if state.intent in (Intent.GENERAL, Intent.UNKNOWN):
            # Not a machine-record request. The general assistant answers it,
            # with whatever machine we are discussing still in its context —
            # asking for a serial here is what made Maia feel robotic.
            validation.intent_understood = state.intent is Intent.GENERAL
            state.response_mode = ResponseMode.PASS
            state.message = ""
            return
        if state.intent is Intent.INVALID_REQUEST:
            state.response_mode = ResponseMode.ASK_SERIAL
            validation.needs_clarification = True
            state.message = "Send me an equipment serial number and I'll look it up."
            return
        if state.intent not in NEEDS_SERIAL:
            state.response_mode = ResponseMode.ASK_SERIAL
            validation.needs_clarification = True
            state.message = ("I look up Caterpillar equipment by serial number. "
                             "Which machine do you need?")
            return

        explicit = [e for e in state.extracted if e.confidence >= 0.5]
        if len(explicit) > 1:
            # Two identifiers in one sentence is a question we cannot answer by
            # picking one. Ask, rather than silently taking the first.
            state.response_mode = ResponseMode.ASK_WHICH_EQUIPMENT
            validation.needs_clarification = True
            context.awaiting = "serial"
            names = ", ".join(e.normalized for e in explicit[:4])
            state.message = f"You mentioned more than one serial ({names}). Which should I look up?"
            return

        if not explicit and _ANOTHER_MACHINE.search(state.utterance):
            # "another machine" is the one follow-up that must NOT inherit the
            # machine we were discussing.
            state.response_mode = ResponseMode.ASK_SERIAL
            validation.needs_clarification = True
            context.awaiting = "serial"
            state.message = ("أكيد — ابعتلي سيريال المعدة التانية." if state.language == "ar"
                             else "Sure — what's the serial number of the other machine?")
            return

        if explicit:
            chosen = explicit[0]
            state.serial_number = chosen.normalized
            state.serial_source = "utterance"
            validation.serial_identified = True
            validation.serial_format_valid = chosen.format_valid
            # A new explicit serial always replaces the conversation's subject.
            if context.active_serial and context.active_serial != chosen.normalized:
                state.notes.append(
                    f"subject changed from {context.active_serial} to {chosen.normalized}")
            context.remember(chosen.normalized, confirmed=False)
        elif context.active_serial:
            # "what about the engine?" — only ever about a machine we have
            # actually established, never about one we merely proposed.
            if not context.confirmed and context.pending_candidates:
                state.response_mode = ResponseMode.ASK_WHICH_EQUIPMENT
                validation.needs_clarification = True
                state.message = "Which machine did you mean? I don't have a confirmed one yet."
                return
            state.serial_number = context.active_serial
            state.serial_source = "context"
            validation.serial_identified = True
            validation.serial_format_valid = format_valid(context.active_serial)
            state.notes.append(f"serial taken from the conversation: {context.active_serial}")
        else:
            state.response_mode = ResponseMode.ASK_SERIAL
            validation.needs_clarification = True
            context.awaiting = "serial"
            state.message = ("Which machine? Send me the serial number — "
                             "for example the PIN stamped on the plate.")
            return

        if not validation.serial_format_valid:
            state.response_mode = ResponseMode.ASK_SERIAL
            validation.needs_clarification = True
            validation.blockers.append("serial_format_invalid")
            context.awaiting = "serial"
            state.message = (f"“{state.serial_number}” doesn't look like an equipment "
                             "serial number. Could you check it and send it again?")
            return

        await self._check_existence(state)

    async def _check_existence(self, state: AgentState) -> None:
        """Format validity is not existence. This is where they are told apart."""
        validation = state.validation
        serial = state.serial_number or ""
        record = None
        if self.repo is not None:
            try:
                rows = await self.repo.get_any_source(serial)
                record = rows[0] if rows else None
            except Exception as exc:
                log(logger, logging.WARNING, "agent.store_read_failed", error=str(exc)[:160])

        if record:
            validation.serial_exists = True
            state.context.remember(serial, confirmed=True)
            state.response_mode = ResponseMode.RUN_LOOKUP
            state.tool_plan = self._plan(state, exists_in_store=True)
            return

        validation.serial_exists = False
        candidates = await resolve.find_candidates(self.repo, serial) if self.repo else []
        state.serial_candidates = candidates
        band = resolve.confidence_band(candidates)

        if band == "high":
            best = candidates[0]
            state.response_mode = ResponseMode.CONFIRM_CANDIDATE
            validation.needs_clarification = True
            state.context.awaiting = "confirmation"
            state.context.pending_candidates = candidates[:1]
            state.context.last_intent = state.intent
            because = f" — {best.explanation}" if best.explanation else ""
            state.message = (f"I couldn't find {serial}. I do have "
                             f"{best.serial_number}{because}. Did you mean that one?")
            return
        if band == "medium":
            state.response_mode = ResponseMode.CHOOSE_CANDIDATE
            validation.needs_clarification = True
            state.context.awaiting = "candidate_choice"
            state.context.pending_candidates = candidates
            state.context.last_intent = state.intent
            listed = "\n".join(f"  • {c.serial_number}" for c in candidates)
            state.message = (f"I couldn't find {serial}. These are close:\n{listed}\n"
                             "Which one did you mean?")
            return

        # Not in the store and nothing close. It may still be a real machine the
        # store has never seen — so we go and ask the source, rather than
        # declaring it does not exist.
        state.response_mode = ResponseMode.RUN_LOOKUP
        state.tool_plan = self._plan(state, exists_in_store=False)
        state.notes.append("not in the store and no near match; the source is the only way to know")

    # ── planning ────────────────────────────────────────────────────────────
    def _plan(self, state: AgentState, *, exists_in_store: bool | None) -> list[ToolCall]:
        """Internal data first, the source only when it must be.

        A question the store can answer never launches a browser, and a targeted
        question is answered from a targeted read.
        """
        serial = state.serial_number or ""
        plan: list[ToolCall] = [ToolCall(
            tool="get_equipment_from_local_store",
            arguments={"serial_number": serial},
            because="the store answers in milliseconds and costs the source nothing")]

        if state.intent is Intent.EQUIPMENT_HISTORY:
            plan.append(ToolCall(tool="get_equipment_history",
                                 arguments={"serial_number": serial, "limit": 10},
                                 because="the user asked how the record changed"))
            return plan

        if state.intent is Intent.STATUS_CHECK and exists_in_store:
            return plan          # "do we have it?" is answered by having it

        wants_fresh = bool(_WANTS_FRESH.search(state.utterance))
        wanted = set(state.requested_fields)

        if exists_in_store and not wants_fresh:
            # We already hold this machine. Driving a browser to re-read what is
            # on disk costs the user 40 seconds and costs Caterpillar a session,
            # for an answer we can give now. Staleness is the gateway's call, and
            # it makes it without our help.
            plan[0].because = (
                "the store already holds this record; the freshness policy decides "
                "on its own whether the source is worth the trip")
            if wanted and not wanted <= STORE_ANSWERABLE:
                plan.append(ToolCall(
                    tool="search_equipment_in_sis",
                    arguments={"serial_number": serial, "reason": "user_request"},
                    because="what was asked for is not a field the store carries"))
            return plan

        plan.append(ToolCall(
            tool="search_equipment_in_sis",
            arguments={"serial_number": serial,
                       "reason": "stale_refresh" if exists_in_store else "user_request"},
            because=("the user asked for current data" if wants_fresh
                     else "the store has no record for this serial")))
        return plan

    # ── the gate ────────────────────────────────────────────────────────────
    def _finalize(self, state: AgentState) -> None:
        validation = state.validation
        if state.response_mode is ResponseMode.RUN_LOOKUP and not state.serial_number:
            validation.blockers.append("no_serial_for_lookup")
            state.response_mode = ResponseMode.ASK_SERIAL
        if validation.needs_clarification and state.response_mode is ResponseMode.RUN_LOOKUP:
            state.response_mode = ResponseMode.ASK_SERIAL
        state.context.last_intent = state.intent


# ── conversation ────────────────────────────────────────────────────────────
_ANOTHER_MACHINE = re.compile(
    r"\b(another|different|other|new|next)\s+(machine|serial|equipment|unit|one)\b"
    r"|(معدة|سيريال|مكنة)\s+(تانية|تاني|تانيه|غير|جديدة|جديد)", re.I)


def small_talk_reply(utterance: str, active_serial: str | None,
                     language: str) -> tuple[str, list[str]]:
    """A natural answer to "thanks", "hi", "ok", "bye" — aware of the machine
    we are discussing, and never claiming a value about it."""
    kind = intents.small_talk_kind(utterance)
    ar = language == "ar"
    sn = active_serial
    if ar:
        follow = (["اعرض القطع", "بيانات المحرك", "تواريخ التصنيع", "سيريال تاني"] if sn
                  else ["دور على سيريال", "إيه اللي تقدري تعمليه؟"])
        if kind == "greeting":
            text = (f"أهلاً! لسه معايا المعدة **{sn}** — تحب نكمل عليها ولا نشوف معدة تانية؟"
                    if sn else "أهلاً! ابعتلي سيريال أي معدة كاتربيلر وأجيبلك بياناتها من SIS.")
        elif kind == "goodbye":
            text = "مع السلامة! أنا هنا في أي وقت."
        elif kind == "ack":
            text = (f"تمام. محتاج حاجة تانية عن **{sn}**؟" if sn
                    else "تمام. ابعتلي السيريال لما تكون جاهز.")
        else:
            text = (f"شكراً! سعيدة إن ده ساعدك. تحب أفصّل أكتر في **{sn}** — القطع، "
                    "بيانات المحرك، ولا تواريخ التصنيع؟" if sn
                    else "شكراً! ابعتلي سيريال تاني في أي وقت.")
        return text, follow if kind != "goodbye" else []
    follow = (["Show the parts", "Engine details", "Build dates", "Another serial"] if sn
              else ["Look up a serial", "What can you do?"])
    if kind == "greeting":
        text = (f"Hi! We were looking at **{sn}** — want to keep going on it, or check "
                "another machine?" if sn else
                "Hi! Send me any Caterpillar serial number and I'll pull its data from SIS.")
    elif kind == "goodbye":
        text = "Bye for now — I'm here whenever you need a machine looked up."
    elif kind == "ack":
        text = (f"Great. Anything else on **{sn}**?" if sn
                else "Great — send me a serial whenever you're ready.")
    else:
        text = (f"Thank you — glad it helped! Want me to go deeper on **{sn}**: the parts "
                "groups, the engine serial and build date, or check another machine?" if sn
                else "Thank you! Send me another serial any time.")
    return text, follow if kind != "goodbye" else []


# ── after the tools have run ────────────────────────────────────────────────
def verify_result(state: AgentState, result: dict[str, Any] | None) -> AgentState:
    """Check a tool's answer before a word of it is repeated.

    The check that matters most: the record we got back must be the record we
    asked for. A source that answers about a different machine has not answered.
    """
    state = state.model_copy(deep=True)
    validation = state.validation

    if not result or not result.get("ok", result.get("status") == "success"):
        validation.tool_returned_data = False
        code = (result or {}).get("error_code") or "INTERNAL_ERROR"
        state.response_mode = (ResponseMode.NOT_FOUND if code == "SERIAL_NOT_FOUND"
                               else ResponseMode.ERROR)
        state.message = explain_error(code, state.serial_number, state.language)
        validation.blockers.append(f"tool_failed:{code}")
        return state

    data = result.get("data") or {}
    attribution = result.get("attribution") or {}
    returned = normalize_serial(str(data.get("serial_number") or ""))
    asked = normalize_serial(state.serial_number or "")

    validation.tool_returned_data = bool(data)
    validation.serial_matches_request = (returned == asked) if (returned and asked) else None
    validation.data_fresh = attribution.get("freshness") in (None, "FRESH")

    if validation.serial_matches_request is False:
        # Never presented as a successful lookup, whatever else came back.
        validation.blockers.append("serial_mismatch")
        state.response_mode = ResponseMode.ERROR
        state.message = (f"I asked for {asked} but the record that came back is for "
                         f"{returned}. I won't report that as your machine — "
                         "please try the lookup again.")
        return state

    # Only fields the tool actually produced may be spoken.
    validation.grounded_fields = sorted(
        key for key, value in data.items() if value not in (None, "", [], {}))
    missing = [f for f in state.requested_fields
               if f not in validation.grounded_fields]
    if missing:
        state.notes.append("not published by the source: " + ", ".join(missing))

    state.response_mode = ResponseMode.ANSWER
    state.tool_results = [result]
    return state


#: What each failure means to the person who asked, in their words, with no
#: stack trace, no selector and no internal path.
ERROR_MESSAGES: dict[str, dict[str, str]] = {
    "SERIAL_NOT_FOUND": {
        "en": "Caterpillar SIS has no record for {serial}. Worth checking the serial on the "
              "plate — a single character is easy to misread.",
        "ar": "مفيش سجل لـ {serial} في Caterpillar SIS. يُفضَّل مراجعة السيريال على المعدة."},
    "LOGIN_FAILED": {
        "en": "I couldn't sign in to Caterpillar SIS, so I can't reach {serial} right now. "
              "That's on our side — the credentials need checking.",
        "ar": "مقدرتش أسجّل دخول على Caterpillar SIS، فمش قادرة أوصل لـ {serial} دلوقتي."},
    "MFA_REQUIRED": {
        "en": "Caterpillar SIS is asking for a verification code. Someone needs to complete "
              "that sign-in by hand before I can continue.",
        "ar": "SIS طالب كود تحقق. محتاج حد يكمّل تسجيل الدخول يدويًا."},
    "CAPTCHA_DETECTED": {
        "en": "Caterpillar SIS showed a security challenge. A person has to clear it before "
              "automation can continue.",
        "ar": "SIS عرض تحدي أمني. محتاج حد يعديه قبل ما أكمل."},
    "WEBSITE_CHANGED": {
        "en": "The Caterpillar SIS pages have changed shape, so I stopped rather than read "
              "them wrongly. Engineering has the run id.",
        "ar": "صفحات SIS اتغيّرت، فوقفت بدل ما أقرأ غلط. الفريق معاه رقم التشغيل."},
    "EXTRACTION_ERROR": {
        "en": "I reached the record for {serial} but couldn't read the fields off it. Nothing "
              "is being guessed, so I have nothing to report.",
        "ar": "وصلت لسجل {serial} بس مقدرتش أقرا البيانات. مش هخمّن حاجة."},
    "PERSISTENCE_FAILED": {
        "en": "I retrieved the data for {serial} but couldn't save it, so I'm not treating the "
              "lookup as complete. Worth retrying.",
        "ar": "جِبت بيانات {serial} بس مقدرتش أحفظها، فمش هعتبر العملية اكتملت."},
    "TIMEOUT": {
        "en": "Caterpillar SIS didn't answer in time for {serial}. Shall I try again?",
        "ar": "SIS مردّش في الوقت المناسب لـ {serial}. أجرّب تاني؟"},
    "NETWORK_ERROR": {
        "en": "I couldn't reach Caterpillar SIS. That's usually the network or the VPN.",
        "ar": "مقدرتش أوصل لـ SIS — غالبًا الشبكة أو الـ VPN."},
    "RATE_LIMITED": {
        "en": "That's a lot of lookups in a short window. Give it a moment and ask again.",
        "ar": "ده عدد كبير من الطلبات في وقت قصير. استنى شوية وجرّب تاني."},
    "INVALID_SERIAL": {
        "en": "That doesn't look like an equipment serial number. Could you check it?",
        "ar": "ده مش شكل سيريال معدة. ممكن تتأكد منه؟"},
    "INVALID_DATA": {
        "en": "What came back for {serial} failed our quality checks, so it wasn't stored and "
              "I won't report it.",
        "ar": "اللي رجع لـ {serial} معدّاش فحص الجودة، فمتخزنش ومش هعرضه."},
    "CIRCUIT_OPEN": {
        "en": "Lookups against Caterpillar SIS are paused after repeated failures. I can still "
              "read anything already stored.",
        "ar": "طلبات SIS متوقفة مؤقتًا بعد أعطال متكررة. لسه أقدر أقرأ المحفوظ."},
}


def explain_error(code: str, serial: str | None, language: str = "en") -> str:
    entry = ERROR_MESSAGES.get(code)
    if not entry:
        return ("Something went wrong on our side and I'd rather say so than guess. "
                "The run id is in the details." if language == "en"
                else "في مشكلة عندنا، وأفضل أقولها بدل ما أخمّن.")
    return entry.get(language, entry["en"]).format(serial=serial or "that serial")
