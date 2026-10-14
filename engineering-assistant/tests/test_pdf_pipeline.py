"""Text normalisation, extraction, OCR, chunking and metadata."""
import pytest

from eka import ocr
from eka.chunking import chunk_pages
from eka.extract import ExtractionError, extract
from eka.textutil import identifiers, numbers, tokens


# ── text utilities ──
def test_tokens_keep_identifiers_whole_and_joined():
    toks = tokens("Replace cartridge 1R–0750 on the HX-200; thread M12x1.5")
    for expected in ("1r-0750", "1r0750", "hx-200", "hx200", "m12x1.5"):
        assert expected in toks


def test_decimals_and_thousands_are_not_split_or_merged():
    toks = tokens("3.50 mm and 1,200 N.m")
    assert "3.50" in toks and "1200" in toks and "350" not in toks


def test_numbers_ignore_digits_inside_identifiers():
    assert numbers("HX-200 rated 350 bar (5,076 psi), 3.50 mm") == {"350", "5076", "3.5"}
    assert identifiers("part 1R-0750 for HX-200") == {"1R-0750", "HX-200"}


# ── extraction ──
def test_native_pdf_pages_tables_and_metadata(samples, settings):
    ex = extract((samples / "HX-200_Service_Manual_RevB.pdf").read_bytes(), settings)
    assert ex.page_count == 6
    assert [p.page_no for p in ex.pages] == [1, 2, 3, 4, 5, 6]
    assert ex.metadata["doc_number"] == "HX-200-SM" and ex.metadata["revision"] == "B"
    tables = [e for e in ex.pages[1].elements if e.kind == "table"]
    assert tables[0].header == ["Parameter", "Value", "Notes"]
    assert tables[0].caption.startswith("Table 1")
    assert ["Rated pressure", "350 bar (5076 psi)", "Continuous duty"] in tables[0].rows
    assert tables[1].header[:3] == ["Fastener", "Thread", "Torque (N.m)"]


def test_blank_page_is_reported_not_dropped(samples, settings):
    ex = extract((samples / "HX-200_Service_Manual_RevB.pdf").read_bytes(), settings)
    blank = ex.pages[4]
    assert blank.page_no == 5 and blank.status == "empty" and "blank" in blank.reason


def test_two_column_page_reads_left_column_first(samples, settings):
    ex = extract((samples / "HX-200_Service_Manual_RevB.pdf").read_bytes(), settings)
    text = "\n".join(e.text for e in ex.pages[3].elements)
    assert text.index("clogged suction") < text.index("Low output pressure") < text.index("1R-0750")


def test_warnings_are_separate_elements(samples, settings):
    ex = extract((samples / "HX-200_Service_Manual_RevB.pdf").read_bytes(), settings)
    warnings = [e.text for p in ex.pages for e in p.elements if e.kind == "warning"]
    assert any(w.startswith("WARNING: Relieve all hydraulic pressure") for w in warnings)
    assert any(w.startswith("CAUTION:") for w in warnings)


@pytest.mark.skipif(not ocr.available(), reason="tesseract is not installed")
def test_scanned_pdf_is_read_by_ocr(samples, settings):
    ex = extract((samples / "TL-5_Inspection_Checklist_scanned.pdf").read_bytes(), settings)
    assert [p.status for p in ex.pages] == ["ocr", "ocr"]
    assert all(p.ocr_confidence and p.ocr_confidence > 80 for p in ex.pages)
    text = " ".join(e.text for e in ex.pages[0].elements)
    assert "40-50 mm" in text
    assert any(e.kind == "warning" for e in ex.pages[0].elements)


def test_scanned_pdf_without_ocr_is_reported(samples, settings):
    settings.ocr_enabled = False
    ex = extract((samples / "TL-5_Inspection_Checklist_scanned.pdf").read_bytes(), settings)
    assert [p.status for p in ex.pages] == ["ocr_unavailable", "ocr_unavailable"]
    assert "OCR is disabled" in ex.pages[0].reason


def test_corrupted_and_non_pdf_inputs_fail_with_reason(samples, settings):
    with pytest.raises(ExtractionError, match="corrupted|no pages"):
        extract((samples / "corrupted.pdf").read_bytes(), settings)
    with pytest.raises(ExtractionError, match="not a PDF"):
        extract(b"hello world", settings)


def test_encrypted_pdf_fails_with_reason(settings):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(ExtractionError, match="password"):
        extract(data, settings)


# ── chunking ──
def test_chunks_keep_page_section_and_table_headers(samples, settings):
    ex = extract((samples / "HX-200_Service_Manual_RevB.pdf").read_bytes(), settings)
    chunks = chunk_pages(ex.pages)
    row = next(c for c in chunks if c.kind == "table_row" and "Port cover bolt" in c.text)
    assert row.page_no == 2
    assert "Torque (N.m): 49" in row.text and "Table 2" in row.text
    assert row.section.endswith("3 Torque Specifications")
    steps = next(c for c in chunks if "1. Drain the pump case" in c.text)
    assert steps.page_no == 3 and "7. Install the retaining ring" in steps.text
    assert all(c.page_no != 5 for c in chunks)  # the blank page yields no chunks
    # every chunk has a single real page number
    assert {c.page_no for c in chunks} <= {1, 2, 3, 4, 6}
