"""
Drop weak vector matches so off-topic questions abstain with no sources.

Pinecone always returns nearest neighbors even when similarity is poor (e.g.
"What is Russia's capital?" vs Arnifi docs). Without a floor, the UI shows
misleading source cards and Nova gets irrelevant context.
"""

from __future__ import annotations

from app.models.schemas import RetrievedChunk
from app.services.retrieval.fee_focus import (
    chunk_matches_service_codes,
    chunk_title_matches_question,
    extract_service_codes,
)
from app.utils.helpers import get_logger

logger = get_logger(__name__)


def is_lexical_rescue_chunk(chunk: RetrievedChunk) -> bool:
    meta = chunk.metadata or {}
    if meta.get("lexical_rescue"):
        return True
    # Bundled / extract inject sets score=1.0
    return float(chunk.score or 0.0) >= 1.0


def bypasses_relevance_gate(question: str, chunk: RetrievedChunk) -> bool:
    """Exact PM#### / Title hits and lexical injects must survive low scores."""
    if is_lexical_rescue_chunk(chunk):
        return True
    codes = extract_service_codes(question)
    if codes and chunk_matches_service_codes(chunk, codes):
        return True
    if chunk_title_matches_question(question, chunk):
        return True
    return False


def apply_relevance_gate(
    matches: list[RetrievedChunk],
    *,
    question: str,
    min_score: float,
) -> list[RetrievedChunk]:
    """
    Keep chunks at or above ``min_score`` (cosine).

    Set ``min_score`` to 0 to disable. Exact code/title / lexical rows bypass.
    """
    if not matches or min_score <= 0:
        return matches

    kept: list[RetrievedChunk] = []
    dropped = 0
    for chunk in matches:
        score = float(chunk.score or 0.0)
        if score >= min_score or bypasses_relevance_gate(question, chunk):
            kept.append(chunk)
        else:
            dropped += 1

    if dropped:
        logger.info(
            "Relevance gate (min_score=%.2f): kept %d, dropped %d weak match(es)",
            min_score,
            len(kept),
            dropped,
        )
    if matches and not kept:
        best = max(float(m.score or 0.0) for m in matches)
        logger.info(
            "Relevance gate: abstaining — best score %.3f below min_score %.2f",
            best,
            min_score,
        )
    return kept
