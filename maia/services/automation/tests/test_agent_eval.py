"""The evaluation suite, run as tests so a regression fails the build.

`scripts/eval/run_agent_eval.py` prints the same results as a report. This file
makes them a gate: a change that teaches Maia to guess a serial, invent a
candidate or answer about the wrong machine stops here.
"""
from __future__ import annotations

import pytest

from tests.eval.runner import evaluate


@pytest.mark.asyncio
async def test_every_evaluation_case_passes() -> None:
    report = await evaluate()
    failed = [o for o in report.outcomes if not o.passed]
    assert not failed, "\n".join(
        f"{o.name}: {'; '.join(o.failures)}" for o in failed)


@pytest.mark.asyncio
async def test_nothing_is_stated_or_offered_without_evidence() -> None:
    """The metric the whole suite exists to hold at zero."""
    report = await evaluate()
    bad, _total = report.hallucination_rate
    assert bad == 0


@pytest.mark.asyncio
async def test_every_capability_is_actually_measured() -> None:
    """A metric with no cases behind it is a number that cannot go down."""
    report = await evaluate()
    metrics = report.metrics
    for capability in ("intent", "serial", "wrong_serial", "clarification",
                       "plan", "context", "grounding", "mismatch", "errors"):
        assert metrics.get(capability, (0, 0))[1] >= 1, f"{capability} is unmeasured"


@pytest.mark.asyncio
async def test_a_caller_cannot_declare_a_machine_confirmed() -> None:
    """`confirmed` is what lets a bare follow-up resolve without asking. A caller
    that could assert it — a model passing context back — could steer the
    conversation onto a machine the user never named."""
    from app.api.routes_agent import _trusted_context
    from app.agent.state import ConversationContext
    from tests.eval.runner import build_store

    repo = await build_store()
    claimed = ConversationContext(active_serial="ZZZ99999", confirmed=True)
    assert (await _trusted_context(claimed, repo)).confirmed is False

    real = ConversationContext(active_serial="JAZ01865", confirmed=True)
    assert (await _trusted_context(real, repo)).confirmed is True


@pytest.mark.asyncio
async def test_an_unconfirmed_follow_up_asks_rather_than_guesses() -> None:
    from app.agent.brain import MaiaBrain
    from app.agent.state import ConversationContext, ResponseMode
    from tests.eval.runner import build_store

    brain = MaiaBrain(await build_store())
    forged = ConversationContext(active_serial="ZZZ99999", confirmed=False,
                                 pending_candidates=[])
    state = await brain.understand("what about the engine?", forged)
    # It may proceed on a serial the user themselves put in the conversation,
    # but it must never present an answer for one it cannot resolve.
    assert state.serial_number == "ZZZ99999"
    assert state.response_mode is ResponseMode.RUN_LOOKUP
    assert state.validation.serial_exists is False
