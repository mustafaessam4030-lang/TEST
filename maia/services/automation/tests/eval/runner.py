"""Run the evaluation set and compute the metrics from what actually happened.

No number in the report is written by hand. Each metric is (passed / attempted)
over the cases that claim to measure it, so a figure can always be traced back
to the cases behind it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agent.brain import MaiaBrain, verify_result
from app.agent.state import AgentState, ConversationContext
from app.models.schemas import EquipmentRecord
from app.repositories.memory_repo import MemoryEquipmentRepository
from tests.eval.cases import CASES, RESULT_CASES, STORE_SERIALS, Case, ResultCase

#: Human names for each measured capability, in report order.
METRIC_NAMES: dict[str, str] = {
    "intent": "Intent Accuracy",
    "serial": "Serial Extraction Accuracy",
    "normalization": "Serial Normalization Accuracy",
    "wrong_serial": "Wrong-Serial Detection",
    "format_vs_existence": "Format vs Existence Separation",
    "clarification": "Clarification Accuracy",
    "plan": "Tool Selection Accuracy",
    "fields": "Field Targeting Accuracy",
    "context": "Context Resolution",
    "grounding": "Tool Result Grounding",
    "mismatch": "Serial Mismatch Protection",
    "errors": "Error Explanation Quality",
    "conversation": "Conversational Handling",
}


@dataclass
class Outcome:
    name: str
    passed: bool
    measures: tuple[str, ...]
    failures: list[str] = field(default_factory=list)


async def build_store() -> MemoryEquipmentRepository:
    """A store holding exactly the serials the cases assume, and nothing else.

    Candidates in the report can therefore only have come from these rows —
    which is the point: a suggestion with no row behind it is a hallucination.
    """
    repo = MemoryEquipmentRepository()
    for serial in STORE_SERIALS:
        await repo.upsert(EquipmentRecord(
            serial_number=serial, source_system="cat_sis",
            retrieved_at=datetime.now(timezone.utc),
            machine_serial_number=serial, machine_build_date="2014-08-02",
            engine_serial_number="PRH04588", engine_build_date="2014-06-30",
            equipment_model="C32", equipment_type="GENERATOR_SET"))
    return repo


async def run_case(brain: MaiaBrain, case: Case) -> tuple[Outcome, AgentState]:
    context: ConversationContext | None = None
    for line in case.preamble:
        context = (await brain.understand(line, context)).context
    state = await brain.understand(case.utterance, context)

    problems: list[str] = []
    if case.intent is not None and state.intent is not case.intent:
        problems.append(f"intent {state.intent.value} != {case.intent.value}")
    if case.no_serial and state.serial_number:
        problems.append(f"expected no serial, got {state.serial_number}")
    if case.serial is not None and state.serial_number != case.serial:
        problems.append(f"serial {state.serial_number} != {case.serial}")
    if case.response_mode is not None and state.response_mode is not case.response_mode:
        problems.append(f"mode {state.response_mode.value} != {case.response_mode.value}")

    planned = [call.tool for call in state.tool_plan]
    for tool in case.plan_includes:
        if tool not in planned:
            problems.append(f"plan missing {tool}")
    for tool in case.plan_excludes:
        if tool in planned:
            problems.append(f"plan should not call {tool}")
    for wanted in case.fields_include:
        if wanted not in state.requested_fields:
            problems.append(f"field {wanted} not requested")

    offered = {c.serial_number for c in state.serial_candidates}
    for expected in case.candidates:
        if expected not in offered:
            problems.append(f"candidate {expected} not offered")
    # A candidate with no row behind it is the failure mode this suite exists for.
    for candidate in state.serial_candidates:
        if candidate.serial_number not in STORE_SERIALS:
            problems.append(f"invented candidate {candidate.serial_number}")

    lowered = (state.message or "").lower()
    for phrase in case.message_contains:
        if phrase.lower() not in lowered:
            problems.append(f"message missing {phrase!r}")

    return Outcome(case.name, not problems, case.measures, problems), state


def run_result_case(case: ResultCase) -> Outcome:
    state = AgentState(utterance=f"get {case.asked_serial}",
                       serial_number=case.asked_serial)
    checked = verify_result(state, case.result)

    problems: list[str] = []
    if checked.response_mode is not case.expect_mode:
        problems.append(f"mode {checked.response_mode.value} != {case.expect_mode.value}")
    lowered = (checked.message or "").lower()
    for phrase in case.message_contains:
        if phrase.lower() not in lowered:
            problems.append(f"message missing {phrase!r}")
    for grounded in case.grounded:
        if grounded not in checked.validation.grounded_fields:
            problems.append(f"{grounded} should be grounded")
    # A null field must never be counted as something we can state.
    data = (case.result or {}).get("data") or {}
    for key, value in data.items():
        if value in (None, "", [], {}) and key in checked.validation.grounded_fields:
            problems.append(f"{key} is null but was marked grounded")
    return Outcome(case.name, not problems, case.measures, problems)


@dataclass
class Report:
    outcomes: list[Outcome]

    @property
    def metrics(self) -> dict[str, tuple[int, int]]:
        """metric -> (passed, attempted), computed from the outcomes only."""
        tally: dict[str, list[int]] = {}
        for outcome in self.outcomes:
            for measure in outcome.measures:
                bucket = tally.setdefault(measure, [0, 0])
                bucket[1] += 1
                bucket[0] += 1 if outcome.passed else 0
        return {k: (v[0], v[1]) for k, v in tally.items()}

    @property
    def hallucination_rate(self) -> tuple[int, int]:
        """Cases where something was stated or offered without evidence."""
        bad = [o for o in self.outcomes
               if any("invented candidate" in f or "should not" in f
                      or "marked grounded" in f for f in o.failures)]
        return len(bad), len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    def render(self) -> str:
        lines = ["", "=" * 72,
                 "  MAIA AGENT EVALUATION",
                 "=" * 72,
                 f"  cases: {self.passed}/{len(self.outcomes)} passed", ""]
        metrics = self.metrics
        for key, label in METRIC_NAMES.items():
            if key not in metrics:
                continue
            passed, attempted = metrics[key]
            pct = (passed / attempted * 100) if attempted else 0.0
            bar = "█" * int(pct / 5) + "·" * (20 - int(pct / 5))
            lines.append(f"  {label:<34} {pct:5.1f}%  {bar}  ({passed}/{attempted})")
        bad, total = self.hallucination_rate
        rate = (bad / total * 100) if total else 0.0
        lines += ["", f"  {'Hallucination Rate':<34} {rate:5.1f}%"
                      f"              ({bad}/{total} cases)"]
        failures = [o for o in self.outcomes if not o.passed]
        if failures:
            lines += ["", "  FAILURES", "  " + "-" * 68]
            for outcome in failures:
                lines.append(f"  ✗ {outcome.name}")
                for problem in outcome.failures:
                    lines.append(f"      {problem}")
        lines += ["", "=" * 72, ""]
        return "\n".join(lines)


async def evaluate() -> Report:
    brain = MaiaBrain(await build_store())
    outcomes = [(await run_case(brain, case))[0] for case in CASES]
    outcomes += [run_result_case(case) for case in RESULT_CASES]
    return Report(outcomes)
