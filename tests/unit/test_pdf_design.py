"""Real narrative PDF checks with controlled original observations."""

import pytest
from pypdf import PdfReader
from tests.unit.test_pdf_renderer import make_draft, renderer


def test_unstructured_prose_does_not_create_visual_scores(tmp_path):
    draft = make_draft(
        summary="normalized_score 99.99; technology 80 [@evidence:ev-fixture]"
    )
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert result.errors == []
    assert result.layout_measurements["visualizations"] == {
        "score_cards": 0,
        "dimension_bars": 0,
        "candidate_rows": 0,
    }


@pytest.mark.parametrize("dated", [True, False])
def test_narrative_timeline_requires_admitted_event_dates(tmp_path, dated):
    from tests.unit.test_pdf_renderer import narrative_fixture

    # Given originals with or without explicit event dates.
    result, context = narrative_fixture(tmp_path, dated=dated)
    # When the actual renderer creates the narrative PDF.
    rendered = renderer(tmp_path / "pdf").render_narrative(
        result, context, "pdf-layout-v1"
    )
    # Then no date is extracted from prose or invented for an undated original.
    assert rendered.errors == []
    metrics = rendered.layout_measurements["visualizations"]
    assert metrics["timeline_events"] == (2 if dated else 0)
    assert metrics["score_cards"] == metrics["dimension_bars"] == 0
    text = "".join(p.extract_text() for p in PdfReader(rendered.artifact_path).pages)
    for eid in result.composition.cited_evidence_ids:
        assert f"[@evidence:{eid}]" in text
    if dated:
        assert "2025-01-10" in text and "2026-01-10" in text
    assert "COMPANY ANALYSIS" in text
