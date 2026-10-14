"""Grounded answers, citations, abstention, clarification, revisions, follow-ups.

Includes the regression guard for the empty-answer problem: every response,
whatever the input, has a non-empty message or claims and a known status.
"""
import pytest

from eka import citations as cite
from eka.textutil import compact, numbers

STATUSES = {"answered", "needs_clarification", "conflict", "insufficient_evidence", "smalltalk",
            "library", "help", "error"}


def ask(assistant, *messages, doc_ids=None):
    cid, out = None, None
    for m in messages:
        out = assistant.engine.ask(m, cid, doc_ids)
        cid = out["conversation_id"]
    return out


def all_claims(answer):
    return answer["claims"] + [c for conflict in answer["conflicts"] for c in conflict["claims"]]


def assert_grounded(assistant, answer):
    """Every citation re-validates and every claim's numbers appear in its cited passages."""
    valid, problems = cite.validate(answer["citations"], assistant.store)
    assert not problems and len(valid) == len(answer["citations"])
    by_id = {c["id"]: c for c in answer["citations"]}
    for claim in all_claims(answer):
        assert claim["citations"], claim
        sources = [assistant.store.chunk(by_id[x]["chunk_id"])["search_text"] for x in claim["citations"]]
        assert cite.unsupported_values(claim["text"], sources) == [], claim


def test_greeting_does_not_retrieve(assistant):
    out = ask(assistant, "Hi, how are you?")
    assert out["status"] == "smalltalk" and out["intent"] == "smalltalk"
    assert "doing well" in out["message"] and out["citations"] == [] and out["trace"] == {}


def test_greeting_with_question_answers_the_question(assistant):
    out = ask(assistant, "Hello! What is the dry weight of the HX-200?")
    assert out["status"] == "answered" and "48 kg" in out["answer_text"]


def test_exact_value_with_page_citation(assistant, docs):
    out = ask(assistant, "What is the rated pressure of the HX-200?")
    assert out["status"] == "answered"
    assert "350 bar (5076 psi)" in out["claims"][0]["text"]
    cited = out["citations"][0]
    assert cited["doc_id"] == docs["HX-200_Service_Manual_RevB.pdf"]["id"] and cited["page"] == 2
    assert cited["open_url"].endswith("#page=2")
    assert out["safety_notice"]  # pressure is safety-relevant
    assert_grounded(assistant, out)


def test_older_revision_value_is_flagged_not_used(assistant, docs):
    out = ask(assistant, "What is the rated pressure of the HX-200?")
    assert "320 bar" not in " ".join(c["text"] for c in out["claims"])
    revision = [c for c in out["conflicts"] if c["kind"] == "revision"]
    assert revision and "320 bar" in revision[0]["claims"][0]["text"]
    old = next(c for c in out["citations"] if c["doc_id"] == docs["HX-200_Service_Manual_RevA.pdf"]["id"])
    assert old["superseded"]


def test_part_number_answer(assistant):
    out = ask(assistant, "Which part number is the relief valve cartridge?")
    assert out["status"] == "answered" and "1R-0750" in out["claims"][0]["text"]
    assert out["citations"][0]["page"] == 4
    assert_grounded(assistant, out)


def test_unit_conversion_only_as_stated(assistant):
    out = ask(assistant, "What is the input power of the CV-90 in hp?")
    assert "3.5 kW (4.7 hp)" in out["claims"][0]["text"]


def test_procedure_includes_all_steps_and_warning(assistant):
    out = ask(assistant, "How do I replace the shaft seal on the HX-200?")
    text = "\n".join(c["text"] for c in out["claims"])
    for step in range(1, 8):
        assert "{0}. ".format(step) in text
    assert any(c["kind"] == "warning" and "relieve hydraulic pressure" in c["text"] for c in out["claims"])
    assert {c["page"] for c in out["citations"]} == {3}
    assert_grounded(assistant, out)


@pytest.mark.parametrize("question", [
    "What is the rated pressure of the HX-300?",
    "What is the warranty period?",
    "What is the warranty period of the HX-200?",
    "Who manufactures the TL-5 engine?",
])
def test_unsupported_questions_abstain(assistant, question):
    out = ask(assistant, question)
    assert out["status"] == "insufficient_evidence", out["answer_text"]
    assert out["claims"] == [] and out["citations"] == []
    assert "don't establish" in out["message"] and "searched" in out["message"]


def test_missing_model_is_named(assistant):
    out = ask(assistant, "What is the rated pressure of the HX-300?")
    assert "HX-300" in out["message"]


def test_ambiguous_equipment_asks_for_clarification(assistant):
    out = ask(assistant, "What torque for the mounting flange bolts?")
    assert out["status"] == "needs_clarification"
    assert len(out["clarification"]["options"]) == 2
    values = " ".join(c["text"] for c in out["claims"])
    assert "180" in values and "210" in values
    assert_grounded(assistant, out)


def test_clarification_reply_resolves(assistant):
    out = ask(assistant, "What torque for the mounting flange bolts?", "the CV-90 gearbox")
    assert out["status"] == "answered"
    assert "180" in out["claims"][0]["text"] and "210" not in out["answer_text"]


def test_follow_up_uses_previous_topic(assistant):
    out = ask(assistant, "What is the rated pressure of the HX-200?", "What about its dry weight?")
    assert out["status"] == "answered" and "48 kg" in out["claims"][0]["text"]


def test_new_question_is_not_hijacked_by_history(assistant):
    out = ask(assistant, "What is the input power of the CV-90?", "What is the warranty period?")
    assert out["status"] == "insufficient_evidence"


def test_ocr_answer_is_labelled(assistant):
    out = ask(assistant, "What is the track sag for the TL-5?")
    assert out["status"] == "answered" and "40-50 mm" in out["claims"][0]["text"]
    assert out["citations"][0]["ocr"] and "OCR" in out["safety_notice"]


def test_document_scope_restricts_answers(assistant, docs):
    cv90 = docs["CV-90_Gearbox_Datasheet.pdf"]["id"]
    out = ask(assistant, "What torque for the mounting flange bolts?", doc_ids=[cv90])
    assert out["status"] == "answered"
    assert {c["doc_id"] for c in out["citations"]} == {cv90}


def test_compare_across_documents(assistant, docs):
    out = ask(assistant, "Compare the mounting flange bolt torque for the HX-200 and the CV-90")
    assert {c["doc_id"] for c in out["citations"]} >= {docs["HX-200_Service_Manual_RevB.pdf"]["id"],
                                                       docs["CV-90_Gearbox_Datasheet.pdf"]["id"]}


def test_library_and_help_intents(assistant):
    assert ask(assistant, "Which documents do you have?")["status"] == "library"
    out = ask(assistant, "What is the rated pressure of the HX-300?", "I cannot find the answer")
    assert out["status"] == "help" and "HX-300" in out["message"]


@pytest.mark.parametrize("message", ["", " ", "?", "hi", "thanks", "asdf qwerty zxcv", "the", "1234",
                                     "What is the" + " very" * 300 + " long question?"])
def test_regression_never_empty_answer(assistant, message):
    out = assistant.engine.ask(message)
    assert out["status"] in STATUSES
    assert out["message"].strip() or out["claims"]
    assert out["answer_text"].strip()


def test_validator_rejects_fabricated_citations(assistant):
    out = ask(assistant, "What is the rated pressure of the HX-200?")
    good = out["citations"][0]
    wrong_page = dict(good, id="X1", page=good["page"] + 1)
    fake_quote = dict(good, id="X2", excerpt="Rated pressure 999 bar")
    unknown = dict(good, id="X3", chunk_id="0000000000000000:1:0")
    valid, problems = cite.validate([good, wrong_page, fake_quote, unknown], assistant.store)
    assert [c["id"] for c in valid] == [good["id"]]
    assert len(problems) == 3


def test_unsupported_values_detects_invented_numbers_and_parts():
    source = "Parameter: Rated pressure | Value: 350 bar (5076 psi)"
    assert cite.unsupported_values("The rated pressure is 350 bar.", [source]) == []
    assert cite.unsupported_values("The rated pressure is 380 bar.", [source]) == ["380"]
    assert cite.unsupported_values("Use seal kit 4J-9999.", [source]) == ["4J-9999"]
    assert numbers("5,076") <= numbers(source) and compact("HX-200") == "hx200"


def test_semantic_similarity_lowers_but_never_removes_the_evidence_bar(assistant):
    """With every passage rated 'strongly similar', unrelated spec rows must still not answer."""
    from eka.answer import AnswerEngine
    from eka.conversation import Conversations
    from eka.retrieval import Retriever
    lenient = Retriever(assistant.index, min_semantic=-1.0)  # every candidate counts as semantically strong
    engine = AnswerEngine(assistant.settings, assistant.store, lenient, Conversations())
    assert engine.ask("What is the HX-200 pump efficiency?")["status"] == "insufficient_evidence"
    out = engine.ask("How much oil does the CV-90 gearbox hold?")
    assert out["status"] == "answered" and "2.4 L" in out["answer_text"]


def test_why_question_returns_cause_and_remedy(assistant):
    out = ask(assistant, "Why can't the HX-200 system reach rated pressure?")
    text = " ".join(c["text"] for c in out["claims"])
    assert "Cause:" in text and "1R-0750" in text and out["citations"][0]["page"] == 4


def test_unnamed_equipment_answer_carries_a_caveat(assistant):
    out = ask(assistant, "What is the gear ratio?")
    assert out["status"] == "answered"
    assert any("doesn't name the equipment" in u for u in out["unresolved"])
