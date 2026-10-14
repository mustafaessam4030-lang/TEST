"""Conversation handling: small talk routing, follow-up resolution, per-conversation memory.

Greetings and thanks are answered without touching retrieval. A follow-up
("what about its weight?") is resolved by adding the previous question's key
terms to the retrieval query and preferring the documents the previous answer
used. A follow-up never overrides the current question: when it names its own
equipment or identifiers, those win and earlier context is not added.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from .textutil import content_terms, identifiers, normalize

GREETING = re.compile(r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening)|greetings|salam|marhaba)\b[\s,!.]*", re.I)
HOW_ARE_YOU = re.compile(r"\b(how are you|how's it going|how are things|how do you do)\b[\s,!.?]*", re.I)
THANKS = re.compile(r"^\s*(thanks|thank you|thx|cheers|great,? thanks|perfect|ok(ay)?,? thanks?)\b[\s,!.]*$", re.I)
BYE = re.compile(r"^\s*(bye|goodbye|see you|that's all|thats all)\b[\s,!.]*$", re.I)
CAPABILITIES = re.compile(r"^\s*(what can you do|who are you|what are you|help|how do (i|you) use (this|you))\b[\s?!.]*$", re.I)
LIBRARY = re.compile(r"\b(what|which|list)\b.*\b(documents?|files?|pdfs?|manuals?)\b.*\b(have|indexed|uploaded|available|loaded|know)\b", re.I)
NOT_FOUND_COMPLAINT = re.compile(r"^\s*(i (can(no|')t|could(n't| not)) find (the|an|this|that)? ?answer|that'?s not (right|it)|not helpful)\b", re.I)

FOLLOW_UP = re.compile(
    r"\b(it|its|it's|this|that|these|those|they|them|their|same|above|previous|mentioned|the one)\b"
    r"|^\s*(and|what about|how about|also|then|so|ok so)\b", re.I)
COMPARE = re.compile(r"\b(compare|comparison|difference|differences|differ|versus|vs\.?)\b", re.I)
QUESTION_START = re.compile(r"^\s*(what|which|how|when|where|who|why|is|are|does|do|can|could|should|list|show|give)\b", re.I)
TROUBLESHOOT = re.compile(r"\b(what (should|do|can) (i|we) do|what to do|how (do i|to|can i) fix|troubleshoot\w*|"
                          r"symptom|cause[sd]?|why)\b", re.I)
PROCEDURE = re.compile(r"\b(how (do|to|should) (i|you|we)?|procedure|steps?|replace|install|remove|disassembl\w*|assembl\w*|adjust)\b", re.I)


@dataclass
class Turn:
    question: str
    retrieval_query: str
    status: str
    doc_ids: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)


@dataclass
class Route:
    intent: str                 # smalltalk | library | not_found_help | question
    smalltalk_kind: str = ""
    question: str = ""          # the question with any leading greeting removed
    retrieval_query: str = ""
    follow_up: bool = False
    clarification_reply: bool = False
    prefer_doc_ids: list[str] = field(default_factory=list)
    compare: bool = False
    procedure: bool = False
    troubleshoot: bool = False


class Conversations:
    """Conversation memory: a bounded in-memory cache over the persistent store.

    With a store, a conversation reopened after a restart still resolves
    follow-ups from its stored turns. Without one (tests), memory only.
    """

    def __init__(self, store=None, max_conversations: int = 500, max_turns: int = 20):
        self._items: OrderedDict[str, list[Turn]] = OrderedDict()
        self._lock = threading.Lock()
        self.store = store
        self.max_conversations, self.max_turns = max_conversations, max_turns

    def new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def history(self, conversation_id: str) -> list[Turn]:
        with self._lock:
            if conversation_id in self._items:
                return list(self._items[conversation_id])
        if self.store is None:
            return []
        turns = [Turn(m["question"], m["retrieval_query"], m["status"], json.loads(m["doc_ids"]), m["created_at"])
                 for m in self.store.messages(conversation_id)][-self.max_turns:]
        with self._lock:
            self._items[conversation_id] = turns
        return list(turns)

    def forget(self, conversation_id: str):
        with self._lock:
            self._items.pop(conversation_id, None)

    def add(self, conversation_id: str, turn: Turn):
        with self._lock:
            turns = self._items.setdefault(conversation_id, [])
            turns.append(turn)
            del turns[: -self.max_turns]
            self._items.move_to_end(conversation_id)
            while len(self._items) > self.max_conversations:
                self._items.popitem(last=False)


def route(message: str, history: list[Turn]) -> Route:
    text = normalize(message)
    if THANKS.match(text):
        return Route("smalltalk", "thanks")
    if BYE.match(text):
        return Route("smalltalk", "bye")
    if CAPABILITIES.match(text):
        return Route("smalltalk", "capabilities")
    if NOT_FOUND_COMPLAINT.match(text):
        return Route("not_found_help")

    # Strip a leading greeting / "how are you"; what remains may be a real question.
    stripped = HOW_ARE_YOU.sub(" ", GREETING.sub(" ", text, count=1)).strip(" ,.!?")
    if LIBRARY.search(stripped) and len(content_terms(stripped)) <= 4:
        return Route("library")
    if not content_terms(stripped):
        return Route("smalltalk", "greeting" if GREETING.match(text) or HOW_ARE_YOU.search(text) else "unclear")

    r = Route("question", question=stripped, retrieval_query=stripped,
              compare=bool(COMPARE.search(stripped)), troubleshoot=bool(TROUBLESHOOT.search(stripped)))
    r.procedure = bool(PROCEDURE.search(stripped)) and not r.troubleshoot
    previous = next((t for t in reversed(history) if t.retrieval_query), None)
    if previous is None:
        return r
    own_ids = identifiers(stripped)
    # A bare fragment ("the gearbox", "CV-90") continues the topic; a full question does not.
    short = len(content_terms(stripped)) <= 2 and not QUESTION_START.match(stripped)
    after_clarification = previous.status == "needs_clarification"
    if FOLLOW_UP.search(stripped) or short or after_clarification:
        r.follow_up = True
        r.clarification_reply = after_clarification
        r.prefer_doc_ids = list(previous.doc_ids)
        if after_clarification or not own_ids:
            # Carry the earlier topic forward; the new words come first so they dominate.
            carried = [t for t in content_terms(previous.retrieval_query) if t not in content_terms(stripped)]
            r.retrieval_query = stripped + " " + " ".join(carried[:8])
        if after_clarification:
            r.prefer_doc_ids = []  # the reply chooses between documents; do not bias it
    return r
