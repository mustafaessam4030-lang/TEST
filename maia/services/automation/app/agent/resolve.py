"""Near-matches for a serial that did not resolve — offered, never applied.

The rule this file exists to enforce: **a typo is the user's to correct.**
JAZ01856 and JAZ01865 are two different machines, and quietly answering about
the second when someone asked about the first is the most damaging thing this
system could do — more damaging than saying "I couldn't find it", because it is
wrong while sounding right.

So candidates are:
  * drawn only from serials that genuinely exist in a store we can read,
  * scored by the mistakes people actually make at a keyboard,
  * and returned for a human to choose between.

Nothing here rewrites an identifier.
"""
from __future__ import annotations

import logging
from typing import Any

from app.agent.state import SerialCandidate
from app.core.logging import log

logger = logging.getLogger(__name__)

#: Characters people confuse when reading a serial off a dusty plate, or when
#: a font renders them alike. A substitution from this table costs less than an
#: arbitrary one, because it is a mistake we expect rather than a coincidence.
CONFUSABLE: dict[str, set[str]] = {
    "0": {"O", "D", "Q"}, "O": {"0", "D", "Q"},
    "1": {"I", "L", "7"}, "I": {"1", "L"}, "L": {"1", "I"},
    "5": {"S"}, "S": {"5"},
    "8": {"B"}, "B": {"8"},
    "2": {"Z"}, "Z": {"2"},
    "6": {"G"}, "G": {"6"},
    "U": {"V"}, "V": {"U"},
}

#: Above this, one candidate is close enough to be worth a yes/no question.
HIGH_CONFIDENCE = 0.86
#: Above this, show the list and let the user pick.
MEDIUM_CONFIDENCE = 0.70
#: The most candidates a person can usefully choose between in a chat message.
MAX_CANDIDATES = 4


def _confusable(a: str, b: str) -> bool:
    return b in CONFUSABLE.get(a, set())


def edit_distance(left: str, right: str) -> int:
    """Damerau-Levenshtein: insertions, deletions, substitutions and the
    transposition that fingers make ("JAZ01865" → "JAZ01856")."""
    if left == right:
        return 0
    rows, cols = len(left) + 1, len(right) + 1
    grid = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        grid[i][0] = i
    for j in range(cols):
        grid[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            grid[i][j] = min(grid[i - 1][j] + 1, grid[i][j - 1] + 1, grid[i - 1][j - 1] + cost)
            if (i > 1 and j > 1 and left[i - 1] == right[j - 2]
                    and left[i - 2] == right[j - 1]):
                grid[i][j] = min(grid[i][j], grid[i - 2][j - 2] + 1)
    return grid[-1][-1]


def similarity(typed: str, known: str) -> tuple[float, str | None]:
    """0..1, plus the human reason when there is one.

    A same-length pair differing by one confusable character scores highest:
    that is not a coincidence, it is someone misreading a 5 for an S. Only an
    identical pair ever reaches 1.0 — a near-match that scored as certainty
    would invite exactly the silent substitution this module forbids.
    """
    if not typed or not known:
        return 0.0, None
    if typed == known:
        return 1.0, None

    distance = edit_distance(typed, known)
    longest = max(len(typed), len(known))
    base = 1.0 - (distance / longest)
    reason: str | None = None

    if len(typed) == len(known):
        differing = [i for i, (a, b) in enumerate(zip(typed, known)) if a != b]
        if len(differing) == 2 and differing[1] == differing[0] + 1:
            i, j = differing
            if typed[i] == known[j] and typed[j] == known[i]:
                base = min(0.99, base + 0.14)
                reason = f"{typed[i]} and {typed[j]} are the other way round"
        if reason is None and len(differing) == 1:
            i = differing[0]
            if _confusable(typed[i], known[i]):
                base = min(0.99, base + 0.12)
                reason = f"{typed[i]} and {known[i]} are easily misread for each other"
            else:
                reason = f"one character differs ({typed[i]} instead of {known[i]})"
        if reason is None and len(differing) == 2:
            reason = "two characters differ"
    elif distance == 1:
        reason = "one character more or fewer"

    # Sharing the alphabetic prefix means the same product family — real evidence.
    prefix = 0
    for a, b in zip(typed, known):
        if a != b:
            break
        prefix += 1
    if prefix >= 3:
        base = min(0.99, base + 0.05)

    return round(max(0.0, min(0.99, base)), 3), reason


async def known_serials(repo: Any, *, limit: int = 500) -> list[str]:
    """Every serial the store can name. Absent capability is not an error —
    a store that cannot list is simply a store that offers no candidates."""
    lister = getattr(repo, "known_serials", None)
    if lister is None:
        return []
    try:
        return list(await lister(limit=limit))
    except Exception as exc:                      # a degraded store, not a failure
        log(logger, logging.WARNING, "agent.candidates_unavailable", error=str(exc)[:160])
        return []


async def find_candidates(repo: Any, typed: str, *,
                          minimum: float = MEDIUM_CONFIDENCE,
                          limit: int = MAX_CANDIDATES) -> list[SerialCandidate]:
    """Serials that exist and look like what the user typed. Best first.

    Every candidate is a real row. Nothing is generated by perturbing the input
    and hoping — a suggestion the store cannot back is a hallucination with a
    confidence score attached.
    """
    typed = (typed or "").upper()
    if not typed:
        return []
    out: list[SerialCandidate] = []
    for known in await known_serials(repo):
        known = (known or "").upper()
        if not known or known == typed:
            continue
        score, why = similarity(typed, known)
        if score < minimum:
            continue
        out.append(SerialCandidate(
            serial_number=known, similarity=score, evidence="internal_store",
            edit_distance=edit_distance(typed, known), explanation=why))
    out.sort(key=lambda c: (-c.similarity, c.edit_distance, c.serial_number))
    return out[:limit]


def confidence_band(candidates: list[SerialCandidate]) -> str:
    """'high' → ask yes/no. 'medium' → offer the list. 'low' → ask again.

    One clearly-closest candidate is a question. Two equally close ones are a
    choice, never a coin toss resolved on the user's behalf.
    """
    if not candidates:
        return "low"
    best = candidates[0]
    if best.similarity >= HIGH_CONFIDENCE:
        runner_up = candidates[1].similarity if len(candidates) > 1 else 0.0
        if best.similarity - runner_up >= 0.05 or len(candidates) == 1:
            return "high"
        return "medium"
    if best.similarity >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"
