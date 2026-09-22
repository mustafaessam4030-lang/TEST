"""The structured state a turn produces before anything is said to the user.

Every field here is decided by code, not by a model. The model may propose an
intent or read a serial out of a sentence; nothing it proposes reaches a tool,
a browser or the store until it has passed through these types and the
validators that fill them.

Three separations carry most of the weight:

  * **format validity is not existence.** `serial_format_valid` says the string
    could be a serial. `serial_exists` says we found the record. JAZ99999 is the
    first without being the second, and conflating them is how a system starts
    confidently answering about machines that do not exist.
  * **a candidate is not a correction.** Near-matches are offered, never
    applied. `response_mode` decides whether we ask, list, or proceed.
  * **grounded is not returned.** `grounded_fields` names what a tool actually
    produced this turn; anything outside it may not be stated as fact.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Intent(str, Enum):
    """What the user is trying to do. Open set by design — UNKNOWN is a valid,
    useful answer that routes to a clarifying question rather than a guess."""

    EQUIPMENT_LOOKUP = "EQUIPMENT_LOOKUP"      # everything about a machine
    EQUIPMENT_DETAILS = "EQUIPMENT_DETAILS"    # a named subset of the record
    PARTS_LOOKUP = "PARTS_LOOKUP"
    ENGINE_DETAILS = "ENGINE_DETAILS"
    BUILD_DATE = "BUILD_DATE"
    EQUIPMENT_HISTORY = "EQUIPMENT_HISTORY"
    STATUS_CHECK = "STATUS_CHECK"              # does this exist / is it current
    HELP = "HELP"
    SMALL_TALK = "SMALL_TALK"                  # thanks, praise, hello, ok, bye
    GENERAL = "GENERAL"                        # a real request, but not about a machine record
    CREDENTIALS = "CREDENTIALS"                # asks for a password/token — always refused
    CLARIFICATION = "CLARIFICATION"            # the user is answering our question
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN = "UNKNOWN"


#: Intents that cannot proceed without knowing which machine we are talking about.
NEEDS_SERIAL = frozenset({
    Intent.EQUIPMENT_LOOKUP, Intent.EQUIPMENT_DETAILS, Intent.PARTS_LOOKUP,
    Intent.ENGINE_DETAILS, Intent.BUILD_DATE, Intent.EQUIPMENT_HISTORY,
    Intent.STATUS_CHECK,
})


class ResponseMode(str, Enum):
    """What the turn should DO. The one decision the model never makes alone."""

    ANSWER = "ANSWER"                    # we have grounded data; state it
    RUN_LOOKUP = "RUN_LOOKUP"            # go to the source
    CONFIRM_CANDIDATE = "CONFIRM_CANDIDATE"   # one near-match; ask before using
    CHOOSE_CANDIDATE = "CHOOSE_CANDIDATE"     # several; let the user pick
    ASK_SERIAL = "ASK_SERIAL"            # no usable identifier
    ASK_WHICH_EQUIPMENT = "ASK_WHICH_EQUIPMENT"   # context is ambiguous
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"
    HELP = "HELP"
    REFUSE = "REFUSE"
    CHAT = "CHAT"                        # conversational reply; no tool, no data claimed
    PASS = "PASS"                        # not an equipment turn — the general assistant answers


class SerialCandidate(BaseModel):
    """A near-match that really exists. Never invented, never auto-applied."""

    model_config = ConfigDict(extra="forbid")

    serial_number: str
    #: 0..1 — how close to what the user typed, by edit distance and the
    #: character confusions people actually make (O/0, S/5, I/1, B/8).
    similarity: float = Field(ge=0.0, le=1.0)
    #: where this candidate came from. Only real stores, never a guess.
    evidence: Literal["internal_store", "history", "source_search"] = "internal_store"
    edit_distance: int = 0
    #: the specific confusions that explain the difference, for the message
    explanation: str | None = None


class ExtractedSerial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: str                      # exactly what the user typed
    normalized: str               # upper, separators and Arabic digits resolved
    confidence: float = Field(ge=0.0, le=1.0)
    #: why we think this token is an identifier and not an ordinary word
    reason: str = ""
    format_valid: bool = False


class Validation(BaseModel):
    """The self-check. Every question answered before a word is generated."""

    model_config = ConfigDict(extra="forbid")

    intent_understood: bool = False
    serial_identified: bool = False
    serial_format_valid: bool = False
    #: None = not checked yet. False is a fact, not an absence.
    serial_exists: bool | None = None
    tool_returned_data: bool | None = None
    serial_matches_request: bool | None = None
    data_fresh: bool | None = None
    needs_clarification: bool = False
    #: fields a tool actually produced this turn — the ONLY facts that may be stated
    grounded_fields: list[str] = Field(default_factory=list)
    #: anything that would make an answer unsafe to give
    blockers: list[str] = Field(default_factory=list)

    @property
    def safe_to_answer(self) -> bool:
        return not self.blockers and not self.needs_clarification


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: why this call, in one line — shown in the audit, not to the user
    because: str = ""


class ConversationContext(BaseModel):
    """What "the engine" and "what about the build date?" refer to.

    Only a serial the user gave explicitly, or confirmed, is ever stored here.
    A candidate we merely proposed is not context until they say yes.
    """

    model_config = ConfigDict(extra="forbid")

    active_serial: str | None = None
    #: True once a lookup returned this record, or the user confirmed it
    confirmed: bool = False
    #: serials mentioned this conversation, newest first — for "the other one"
    recent_serials: list[str] = Field(default_factory=list)
    #: set when we asked a question, so the next turn can be read as its answer
    awaiting: Literal["serial", "candidate_choice", "confirmation", None] = None
    pending_candidates: list[SerialCandidate] = Field(default_factory=list)
    last_intent: Intent | None = None

    def remember(self, serial: str, *, confirmed: bool) -> None:
        self.active_serial = serial
        self.confirmed = confirmed
        self.recent_serials = [serial] + [s for s in self.recent_serials if s != serial][:9]


class AgentState(BaseModel):
    """One turn, fully described. This is what the model is given to speak from."""

    model_config = ConfigDict(extra="forbid")

    utterance: str
    language: Literal["en", "ar"] = "en"
    intent: Intent = Intent.UNKNOWN
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    serial_number: str | None = None
    serial_source: Literal["utterance", "context", "candidate", None] = None
    extracted: list[ExtractedSerial] = Field(default_factory=list)
    serial_candidates: list[SerialCandidate] = Field(default_factory=list)

    #: which fields the user actually asked about; empty means "the record"
    requested_fields: list[str] = Field(default_factory=list)
    context: ConversationContext = Field(default_factory=ConversationContext)
    tool_plan: list[ToolCall] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)
    response_mode: ResponseMode = ResponseMode.ASK_SERIAL
    #: a plain-language line the UI can show verbatim when no model is in the loop
    message: str = ""
    #: short follow-ups the UI may offer as buttons
    suggestions: list[str] = Field(default_factory=list)
    #: for the audit trail, never shown to the user
    notes: list[str] = Field(default_factory=list)
