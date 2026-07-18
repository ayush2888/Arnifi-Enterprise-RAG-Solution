"""Retrieval post-processing — provider-agnostic."""

from __future__ import annotations

from app.models.schemas import RetrievedChunk


def diversify_chunks(
    matches: list[RetrievedChunk],
    *,
    max_chunks_returned: int = 5,
    max_chunks_per_source_url: int = 2,
) -> list[RetrievedChunk]:
    # Without this cap, all top hits might come from one long blog post.
    selected: list[RetrievedChunk] = []
    per_source: dict[str, int] = {}

    for match in sorted(matches, key=lambda m: m.score, reverse=True):
        used_from_url = per_source.get(match.source_url, 0)
        if used_from_url >= max_chunks_per_source_url:
            continue

        selected.append(match)
        per_source[match.source_url] = used_from_url + 1
        if len(selected) >= max_chunks_returned:
            break

    return selected
