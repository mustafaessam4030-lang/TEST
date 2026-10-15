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
# everyday conversation, answered like a colleague would
WELLBEING = re.compile(r"^\s*((are|r) (you|u) (good|ok(ay)?|fine|alright|there|awake|alive|working|ready|online)|"
                       r"(you|u) (good|ok(ay)?|there)|how('?s| is) (it going|your day|everything)|what'?s up|sup|wassup)\b[\s?!.]*$", re.I)
ACK = re.compile(r"^\s*(ok(ay)?|k|kk|cool|great|nice|good|fine|alright|all right|got it|sure|yes|yeah|yep|no|nope|"
                 r"understood|i see|makes sense|right|hmm+|ah+|oh)\b[\s,!.]*$", re.I)
NEED_HELP = re.compile(r"^\s*((i )?(need|want) (some )?help|help( me)?( please)?|please help|can you help( me)?|"
                       r"could you help( me)?|i('m| am) (stuck|lost|confused)|where do i start|how do i start)\b[\s?!.]*$", re.I)
IDENTITY = re.compile(r"^\s*(what'?s your name|what is your name|your name|who (made|built|created) you|"
                      r"are you (a )?(human|bot|robot|ai|real))\b[\s?!.]*$", re.I)
PRAISE = re.compile(r"^\s*(good job|well done|nice work|great work|amazing|awesome|excellent|you('re| are) (great|good|"
                    r"amazing|awesome|smart|helpful))\b[\s,!.]*$", re.I)
SORRY = re.compile(r"^\s*(sorry|my bad|oops)\b[\s,!.]*$", re.I)
NOT_FOUND_COMPLAINT = re.compile(r"^\s*(i (can(no|')t|could(n't| not)) find (the|an|this|that)? ?answer|that'?s not (right|it)|not helpful)\b", re.I)

FOLLOW_UP = re.compile(
    r"\b(it|its|it's|this|that|these|those|they|them|their|same|above|previous|mentioned|the one)\b"
    r"|^\s*(and|what about|how about|also|then|so|ok so)\b", re.I)
COMPARE = re.compile(r"\b(compare|comparison|difference|differences|differ|versus|vs\.?)\b", re.I)
QUESTION_START = re.compile(r"^\s*(what|which|how|when|where|who|why|is|are|does|do|can|could|should|list|show|give)\b", re.I)
TROUBLESHOOT = re.compile(r"\b(what (should|do|can) (i|we) do|what to do|how (do i|to|can i) fix|troubleshoot\w*|"
                          r"symptom|cause[sd]?|why)\b", re.I)
# Answer modes (Luma). Checked in this order; the first match wins.
SIMPLIFY = re.compile(r"\b(simpler|more simply|plain (english|language|terms|words)|layman'?s? terms|eli5|"
                      r"in simple (terms|words)|easier to understand)\b", re.I)
WHERE = re.compile(r"\b(where (is|was|are|were) (that|this|it|these|those)\b|which page|what page|"
                   r"where (did|does) (that|this|it) come from|(source|reference) for (that|this))", re.I)
REVISION_DIFF = re.compile(r"\b(what('s| has| have)? changed|changes? (between|from|in (the )?(new|latest|current))|"
                           r"differences? between (the )?rev|compare (the )?(rev\b|revisions?)|"
                           r"rev(ision)? \w+ (and|vs\.?|versus|to) rev(ision)?)", re.I)
DIFFERENCES = re.compile(r"\b(summari[sz]e|list|show|what are) (me )?the (key |main )?differences\b", re.I)
SUMMARY = re.compile(r"\b(summari[sz]e|summary|overview|key (specs|specifications|points|values|data|ratings)|"
                     r"main (specs|specifications|points)|(important|key|main|all|the) (safety )?(warnings|cautions)|"
                     r"safety (warnings|precautions|notes))\b", re.I)
VISUAL = re.compile(r"\b(picture|photo\w*|image|diagram|schematic|drawing|figure|illustration|circuit|"
                    r"exploded view|wiring|graph|chart)s?\b", re.I)
EXPLAIN = re.compile(r"^\s*(explain|why\b|how does|how do (they|these|those|it)|describe|what does .{1,60} mean|"
                     r"what is the purpose|what's the purpose|tell me (more )?about)|\bin (more )?detail\b", re.I)
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
    mode: str = "direct"        # direct | explain | summary | compare | procedure | visual | where | simplify | revision_diff
    differences_only: bool = False


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
    for pattern, kind in ((WELLBEING, "how_are_you"), (ACK, "ack"), (NEED_HELP, "help"), (IDENTITY, "identity"),
                          (PRAISE, "praise"), (SORRY, "sorry")):
        if pattern.match(text):
            return Route("smalltalk", kind)
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
        return Route("smalltalk", "how_are_you" if HOW_ARE_YOU.search(text) else
                     "greeting" if GREETING.match(text) else "unclear")

    r = Route("question", question=stripped, retrieval_query=stripped,
              compare=bool(COMPARE.search(stripped)), troubleshoot=bool(TROUBLESHOOT.search(stripped)))
    r.procedure = bool(PROCEDURE.search(stripped)) and not r.troubleshoot
    previous = next((t for t in reversed(history) if t.retrieval_query), None)
    r.mode = _mode(stripped, r, previous)
    if r.mode in ("where", "simplify"):
        # These act on the previous answer itself, not on a new search.
        r.follow_up = True
        if previous is not None:
            r.retrieval_query = previous.retrieval_query
            r.prefer_doc_ids = list(previous.doc_ids)
        return r
    if r.mode == "compare" and DIFFERENCES.search(stripped):
        r.differences_only = True
    if previous is None:
        return r
    own_ids = identifiers(stripped)
    # A bare fragment ("the gearbox", "CV-90") continues the topic; a full question does not.
    short = len(content_terms(stripped)) <= 2 and not QUESTION_START.match(stripped)
    after_clarification = previous.status == "needs_clarification"
    if r.differences_only:
        # "Summarize the differences": the same comparison again, reported as differences
        r.follow_up, r.compare, r.mode = True, True, "compare"
        r.prefer_doc_ids = list(previous.doc_ids)
        r.retrieval_query = previous.retrieval_query
        return r
    if r.compare and FOLLOW_UP.search(stripped) and own_ids:
        # "Compare it with the CV-90" / "summarize the differences": keep the earlier topic
        # and its equipment, and add whatever the new message names.
        r.follow_up = True
        r.compare = True
        r.prefer_doc_ids = list(previous.doc_ids)
        carried = [t for t in content_terms(previous.retrieval_query) if t not in content_terms(stripped)]
        r.retrieval_query = stripped + " " + " ".join(carried[:10])
        return r
    if after_clarification and r.mode == "direct":
        # the reply picks an option; the mode comes from the question that was asked
        earlier = _mode(previous.retrieval_query, Route("question", compare=bool(COMPARE.search(
            previous.retrieval_query)), procedure=bool(PROCEDURE.search(previous.retrieval_query))), None)
        if earlier in ("compare", "summary", "revision_diff", "procedure", "explain", "visual"):
            r.mode = earlier
            r.compare = earlier == "compare"
            r.procedure = earlier == "procedure"
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


def _mode(text: str, r: Route, previous: Turn | None) -> str:
    if previous is not None and SIMPLIFY.search(text):
        return "simplify"
    if previous is not None and WHERE.search(text):
        return "where"
    if REVISION_DIFF.search(text):
        return "revision_diff"
    if DIFFERENCES.search(text) and previous is not None:
        return "compare"
    if SUMMARY.search(text):
        return "summary"
    if VISUAL.search(text):
        return "visual"
    if r.compare:
        return "compare"
    if EXPLAIN.search(text):
        return "explain"
    if r.procedure:
        return "procedure"
    return "direct"
