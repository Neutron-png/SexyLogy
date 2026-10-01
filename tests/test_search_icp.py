from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

pytest.importorskip("pypdf")

from app.core.search import icp


def _make_docx(tmp_path, text):
    from docx import Document
    p = tmp_path / "icp.docx"
    d = Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    d.save(str(p))
    return p


def _make_pdf(tmp_path, text):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject
    p = tmp_path / "icp.pdf"
    w = PdfWriter()
    page = w.add_blank_page(width=612, height=792)
    stream_lines = ["BT /F1 12 Tf 72 720 Td"]
    y = 720
    for line in text.splitlines():
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream_lines.append(f"1 0 0 1 72 {y} Tm ({esc}) Tj")
        y -= 16
    stream_lines.append("ET")
    content = "\n".join(stream_lines).encode("latin-1", errors="replace")
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    resources = DictionaryObject()
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font})
    page[NameObject("/Resources")] = resources
    from pypdf.generic import StreamObject
    stream = StreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = w._add_object(stream)
    with open(p, "wb") as f:
        w.write(f)
    return p


SAMPLE = """Ideal Customer Profile

Target industry: swimming pool construction and landscape lighting companies.
We want residential pool builders, 10-100 employees, in Los Angeles and Miami.
Keywords: pool construction, landscape lighting, outdoor design.
Exclusions: pool chemical retailers, franchise resellers.
Decision makers: owners, operations managers (from their own published pages only).
"""


def test_docx_parsing(tmp_path):
    p = _make_docx(tmp_path, SAMPLE)
    text = icp.extract_text(p)
    assert "pool builders" in text or "pool construction" in text


def test_pdf_parsing(tmp_path):
    p = _make_pdf(tmp_path, SAMPLE)
    text = icp.extract_text(p)
    assert "pool" in text.lower()


def test_unreadable_document_raises_cleanly(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"%PDF-1.4 total garbage \xff\xfe")
    with pytest.raises(ValueError):
        icp.extract_text(p)


def test_txt_parsing_and_hash(tmp_path):
    p = tmp_path / "icp.txt"
    p.write_text(SAMPLE, encoding="utf-8")
    assert "pool" in icp.extract_text(p)
    assert len(icp.file_hash(p)) == 64


def test_deterministic_draft(tmp_path):
    draft = icp.draft_from_text(SAMPLE)
    assert draft["keywords"], "draft produced no keywords"
    assert all(k not in icp._STOPWORDS for k in draft["keywords"])


def test_ai_fallback_without_key(tmp_path):
    from app.core.storage.db import Database
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        db = Database(Path(t) / "icp.db")
        profile, notes = icp.ai_profile_from_text(SAMPLE, db)
        assert profile["keywords"], "fallback draft empty"
        assert any("no AI key" in n for n in notes)
        db.close()


def test_profile_from_ui_is_the_authority():
    values = {
        "industries": "pool construction, lighting",
        "keywords": "pool, outdoor",
        "locations": "Los Angeles, Miami",
        "exclusions": "chemical retailers",
    }
    profile = icp.profile_from_ui(values)
    assert profile["industries"] == ["pool construction", "lighting"]
    assert profile["locations"] == ["Los Angeles", "Miami"]
    assert profile["roles"] == []   # untouched fields stay empty lists


def test_query_generation(tmp_path):
    profile = {
        "industries": ["pool construction", "landscape lighting"],
        "keywords": ["outdoor design"],
        "locations": ["Los Angeles", "Miami"],
    }
    queries = icp.generate_queries(profile, max_queries=20)
    assert queries, "no queries generated"
    assert any("pool construction" in q and "Los Angeles" in q for q in queries)
    assert len(set(q.lower() for q in queries)) == len(queries)   # no dupes
    # cap respected
    assert len(icp.generate_queries(profile, max_queries=3)) == 3


def test_query_generation_empty_profile():
    assert icp.generate_queries(icp.empty_profile()) == []


def test_source_qualification_scores_and_reasons():
    profile = {
        "industries": ["pool construction"],
        "keywords": ["lighting"],
        "exclusions": ["chemical"],
    }
    good = icp.qualify_source("https://poolspros.com/a", "Pool Construction Pros",
                              "We build pools with landscape lighting", profile)
    assert good["score"] >= 25 and any("match" in r for r in good["reasons"])

    bad = icp.qualify_source("https://chemshop.com/a", "Pool Chemicals Shop",
                             "retail chemicals", profile)
    assert bad["score"] < good["score"]
    assert any("excluded" in r for r in bad["reasons"])

    social = icp.qualify_source("https://www.facebook.com/page", "Page", "x", profile)
    assert social["score"] == 0


def test_save_and_list_profiles(tmp_path):
    from app.core.storage.db import Database
    with __import__("tempfile").TemporaryDirectory() as t:
        db = Database(Path(t) / "icp.db")
        pid = icp.save_profile(db, "Test ICP", "icp.txt", "h" * 64,
                               {"industries": ["pools"], "keywords": []})
        rows = icp.list_profiles(db)
        assert rows[0]["id"] == pid
        assert rows[0]["profile"]["industries"] == ["pools"]
        assert rows[0]["source_hash"] == "h" * 64
        db.close()
