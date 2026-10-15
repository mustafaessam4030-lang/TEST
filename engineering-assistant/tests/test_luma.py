"""Luma: natural answers, answer modes, figures. Real ingestion of the synthetic samples, no mocks."""
import pytest
from fastapi.testclient import TestClient

from eka.api import create_app
from eka.compose import numeric_value, row_cells, row_sentence
from eka.textutil import numbers


def ask(assistant, *questions):
    conv, out = None, None
    for q in questions:
        out = assistant.engine.ask(q, conv)
        conv = out["conversation_id"]
    return out


# ── direct answers in Luma's voice ──
def test_direct_answer_is_a_natural_sentence_with_the_exact_value(assistant):
    out = ask(assistant, "What is the oil capacity of the CV-90?")
    assert out["status"] == "answered" and out["message"] == ""
    claim = out["claims"][0]
    assert claim["text"] == "The CV-90's oil capacity is 2.4 L."
    assert claim["quote"] == "Parameter: Oil capacity | Value: 2.4 L"          # what the page says
    cited = next(c for c in out["citations"] if c["id"] == claim["citations"][0])
    assert cited["page"] == 1 and "Oil capacity | Value: 2.4 L" in cited["excerpt"]
    assert "according to" not in out["answer_text"].lower()


def test_condition_stays_with_the_value(assistant):
    out = ask(assistant, "What is the rated pressure of the HX-200 pump?")
    assert out["claims"][0]["text"] == "The HX-200's rated pressure is 350 bar (5076 psi), continuous duty."
    assert out["conflicts"] and "320 bar" in out["conflicts"][0]["claims"][0]["text"]   # Rev A, flagged apart
    out = ask(assistant, "What is the rated flow of the HX-200?")
    assert out["claims"][0]["text"] == "The HX-200's rated flow is 120 L/min at 1800 rpm."


def test_composed_sentences_never_change_a_value(assistant):
    """Every specification row in the library: the sentence states only numbers the row contains."""
    chunks, *_ = assistant.retriever.index.snapshot()
    rows = [c for c in chunks if c.kind == "table_row"]
    assert len(rows) >= 15
    for c in rows:
        sentence = row_sentence(c.text, "HX-200", "torque thread value")
        if sentence:
            assert numbers(sentence) <= numbers(c.text + " HX-200"), (sentence, c.text)


def test_ambiguous_question_still_asks_first(assistant):
    out = ask(assistant, "What torque for the mounting flange bolts?")
    assert out["status"] == "needs_clarification" and out["claims"] == []
    assert "180" not in out["answer_text"] and "210" not in out["answer_text"]


def test_missing_information_is_said_plainly(assistant):
    out = ask(assistant, "What is the maximum lifting capacity of the TL-5?")
    assert out["status"] == "insufficient_evidence" and out["claims"] == []
    assert out["message"].startswith("I couldn't verify the maximum lifting capacity of the TL-5")


# ── comparison ──
def test_compare_follow_up_builds_a_table_and_a_labelled_chart(assistant):
    out = ask(assistant, "What torque for the mounting flange bolts on the HX-200?", "Compare it with the CV-90")
    assert out["status"] == "answered" and out["mode"] == "compare"
    rows = {r["cells"][0]: r for r in out["table"]["rows"]}
    assert set(rows) == {"HX-200", "CV-90"}
    assert rows["HX-200"]["cells"][2].startswith("210 N.m") and rows["CV-90"]["cells"][2].startswith("180 N.m")
    assert all(r["citations"] for r in rows.values())
    assert "while" in out["claims"][0]["text"]
    assert out["visual"]["kind"] == "bars" and "Conceptual" in out["visual"]["label"]
    assert out["visual"]["unit"] == "N.m"


def test_chart_only_for_like_for_like_values():
    assert numeric_value("350 bar (5076 psi)") == (350.0, "bar")
    assert numeric_value("-20 to 80 C") is None          # a range is not one value
    assert numeric_value("4J-5521") is None and numeric_value("32:1") is None
    cells = row_cells("T\nParameter: Peak pressure | Value: 420 bar (6092 psi) | Notes: Maximum 10 s")
    assert cells["condition"] == "Maximum 10 s"


def test_compare_with_one_side_missing_says_so(assistant):
    out = ask(assistant, "Compare the rated pressure of the HX-200 and the CV-90")
    assert out["table"] is None and "nothing to compare" in out["message"] and "CV-90" in out["message"]


# ── summaries and revisions ──
def test_document_summary_outlines_sections_with_citations(assistant):
    out = ask(assistant, "Summarize the HX-200 manual")
    assert out["status"] == "answered" and out["mode"] == "summary"
    headings = [c["heading"] for c in out["claims"] if c["kind"] == "outline"]
    assert "Specifications" in headings and "Shaft Seal Replacement" in headings and "Hydraulic Circuit" in headings
    assert all(c["citations"] for c in out["claims"])
    assert "Rev B" in out["message"]                          # the current revision, not Rev A


def test_key_specifications_table(assistant):
    out = ask(assistant, "Give me the key specifications of the CV-90")
    cells = {r["cells"][0]: r["cells"][1] for r in out["table"]["rows"]}
    assert cells["Oil capacity"] == "2.4 L" and cells["Output speed"] == "45 rpm"


def test_safety_warnings_summary_lists_only_warnings(assistant):
    out = ask(assistant, "What are the important safety warnings?")
    assert out["status"] == "answered" and out["claims"]
    assert all(c["kind"] == "warning" and c["citations"] for c in out["claims"])


def test_revision_differences(assistant):
    out = ask(assistant, "What changed between Rev A and Rev B of the HX-200 manual?")
    assert out["mode"] == "revision_diff" and out["table"]["columns"][1:3] == ["Rev A", "Rev B"]
    rows = {r["cells"][0]: r["cells"][1:] for r in out["table"]["rows"]}
    assert rows["Rated pressure"] == ["320 bar (4641 psi)", "350 bar (5076 psi)"]
    assert all(len(r["citations"]) == 2 for r in out["table"]["rows"])   # both revisions cited


# ── follow-ups ──
def test_where_is_that_shown_points_to_the_page(assistant):
    out = ask(assistant, "What is the oil capacity of the CV-90?", "Where is that shown in the manual?")
    assert out["status"] == "answered" and out["mode"] == "where"
    assert out["claims"][0]["text"].startswith("Page 1 of the CV-90 Conveyor Gearbox Datasheet")
    assert out["citations"][0]["page"] == 1


def test_simpler_terms_without_a_model_does_not_paraphrase(assistant):
    out = ask(assistant, "What is the oil capacity of the CV-90?", "Explain that in simpler terms")
    assert out["status"] == "help" and out["claims"] == [] and "language model" in out["message"]


def test_suggested_follow_ups_are_answerable(assistant):
    out = ask(assistant, "What torque for the mounting flange bolts on the HX-200?")
    assert "Where is that shown in the manual?" in out["follow_ups"]
    assert "Compare it with the CV-90" in out["follow_ups"]
    nxt = ask(assistant, "What torque for the mounting flange bolts on the HX-200?", "Compare it with the CV-90")
    assert nxt["status"] == "answered"


def test_procedure_has_a_labelled_conceptual_flow(assistant):
    out = ask(assistant, "How do I replace the shaft seal on the HX-200?")
    assert out["message"] == "Here's the shaft seal replacement procedure for the HX-200:"
    assert out["visual"]["kind"] == "flow" and len(out["visual"]["steps"]) == 7
    assert "not a manufacturer drawing" in out["visual"]["label"]
    assert any(c["kind"] == "warning" for c in out["claims"])


# ── figures ──
def test_figures_are_found_at_ingest(assistant, docs):
    figures = assistant.store.figures(docs["HX-200_Service_Manual_RevB.pdf"]["id"])
    assert [(f["page_no"], f["kind"]) for f in figures] == [(7, "drawing"), (7, "image")]
    assert figures[0]["caption"] == "Figure 1 HX-200 hydraulic circuit (open loop)"
    assert "Relief valve" in figures[0]["labels"] and figures[0]["section"].endswith("Hydraulic Circuit")
    scanned = assistant.store.figures(docs["TL-5_Inspection_Checklist_scanned.pdf"]["id"])
    assert scanned == []                                  # full-page scans are pages, not figures


def test_show_me_returns_the_original_figure(assistant):
    out = ask(assistant, "Show me the hydraulic circuit")
    assert out["status"] == "answered" and out["mode"] == "visual"
    assert [f["caption"] for f in out["figures"]] == ["Figure 1 HX-200 hydraulic circuit (open loop)"]
    assert out["figures"][0]["page"] == 7 and out["figures"][0]["citations"]
    assert any("380 bar" in c["text"] for c in out["claims"])
    photo = ask(assistant, "Show me a picture of the HX-200 pump")
    assert photo["figures"][0]["kind"] == "image"


def test_no_figure_is_said_plainly(assistant):
    out = ask(assistant, "Show me the wiring diagram of the CV-90")
    assert out["status"] == "insufficient_evidence" and not out["figures"]
    assert "couldn't find a figure" in out["message"]


def test_figure_api_renders_the_page_crop(samples, tmp_path):
    from eka.config import load_settings
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        c.post("/api/documents?filename=HX-200_Service_Manual_RevB.pdf",
               content=(samples / "HX-200_Service_Manual_RevB.pdf").read_bytes())
        import time
        deadline = time.time() + 120
        while time.time() < deadline and c.get("/api/documents").json()["documents"][0]["status"] != "ready":
            time.sleep(0.2)
        doc = c.get("/api/documents").json()["documents"][0]
        assert doc["figure_count"] == 2
        figures = c.get("/api/documents/{0}/figures".format(doc["id"])).json()["figures"]
        png = c.get(figures[0]["image_url"])
        assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
        assert c.get("/api/figures/nope/image").status_code == 404


# ── language-model claims (validation only; the model itself is tested in test_llm_live.py) ──
@pytest.mark.parametrize("text,ok", [
    ("Rated flow is the volume of fluid a pump delivers per minute at its rated speed.", True),
    ("Pumps like this usually run at 1500 rpm.", False),          # a number is never general knowledge
    ("The HX-200 is a robust pump.", False),                        # nor is a model name
])
def test_general_knowledge_claims_cannot_carry_specific_values(assistant, text, ok):
    data = {"status": "answered", "claims": [
        {"text": "The CV-90's oil capacity is 2.4 L.", "sources": ["S1"], "kind": "direct"},
        {"text": text, "sources": [], "kind": "general"}]}
    chunks, *_ = assistant.retriever.index.snapshot()
    row = next(c for c in chunks if "Oil capacity" in c.text)
    docs = {d["id"]: d for d in assistant.store.documents()}
    draft = assistant.engine._llm_claims(data, [("S1", row, docs[row.doc_id])], docs)
    general = [c for c in draft.claims if c["kind"] == "general"]
    assert (len(general) == 1) is ok
