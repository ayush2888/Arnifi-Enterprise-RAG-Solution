"""Tests for cosine min_score relevance gate."""

from __future__ import annotations

from app.models.schemas import RetrievedChunk
from app.services.retrieval.relevance_gate import apply_relevance_gate


def _chunk(
    chunk_id: str,
    score: float,
    *,
    text: str = "Some Arnifi doc text",
    metadata: dict | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        score=score,
        source_url=f"https://drive.google.com/file/d/{chunk_id}/view",
        doc_title="Doc",
        heading_path="Folder",
        chunk_text=text,
        metadata=metadata or {},
    )


def test_drops_weak_off_topic_matches() -> None:
    weak = [
        _chunk("a", 0.32),
        _chunk("b", 0.38),
        _chunk("c", 0.41),
    ]
    out = apply_relevance_gate(
        weak, question="What is Russia's capital?", min_score=0.45
    )
    assert out == []


def test_keeps_strong_matches() -> None:
    matches = [
        _chunk("weak", 0.40),
        _chunk("good", 0.72),
    ]
    out = apply_relevance_gate(
        matches, question="Cayman company KYC requirements", min_score=0.45
    )
    assert len(out) == 1
    assert out[0].chunk_id == "good"


def test_lexical_rescue_bypasses_low_score() -> None:
    lexical = _chunk(
        "lex",
        1.0,
        text="PM ID: PM1122\nArnifi fees: 999",
        metadata={"lexical_rescue": True},
    )
    out = apply_relevance_gate(
        [lexical],
        question="Arnifi fee for PM1122?",
        min_score=0.45,
    )
    assert len(out) == 1


def test_exact_pm_code_in_chunk_bypasses() -> None:
    row = _chunk(
        "pm",
        0.42,
        text="PM ID: PM1122\nArnifi fees: 999\nCurrency: AED",
    )
    out = apply_relevance_gate(
        [row],
        question="What is the fee for PM1122?",
        min_score=0.45,
    )
    assert len(out) == 1


def test_exact_title_phrase_bypasses() -> None:
    title = "Malaysia Corporate Compliance and Tax Services"
    row = _chunk(
        "my",
        0.44,
        text=f"Title: {title}\nArnifi fees: 6,869.00\nCurrency: USD",
    )
    q = f"{title} fee?"
    out = apply_relevance_gate([row], question=q, min_score=0.45)
    assert len(out) == 1


def test_min_score_zero_disables_gate() -> None:
    weak = [_chunk("a", 0.12)]
    out = apply_relevance_gate(
        weak, question="What is Russia's capital?", min_score=0.0
    )
    assert len(out) == 1
