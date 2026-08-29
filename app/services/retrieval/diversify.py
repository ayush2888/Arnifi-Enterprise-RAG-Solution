"""Retrieval post-processing — provider-agnostic."""

from __future__ import annotations

from app.models.schemas import RetrievedChunk


def diversify_chunks(
    matches: list[RetrievedChunk],
    *,
    max_chunks_returned: int = 5,
    max_chunks_per_source_url: int = 2,
    protected_chunk_ids: set[str] | None = None,
) -> list[RetrievedChunk]:
    """
    Cap how many chunks come from one source URL.

    Walks ``matches`` in the order provided (callers already rank via score /
    fee_focus / service-code boost). Re-sorting by score here would undo boosts.

    ``protected_chunk_ids`` (e.g. exact PM#### hits) bypass the per-URL cap so
    a typed service code is not dropped behind neighbor SKUs from the same sheet.
    Protected rows still count toward ``max_chunks_returned``.
    """
    selected: list[RetrievedChunk] = []
    per_source: dict[str, int] = {}
    protected = protected_chunk_ids or set()

    for match in matches:
        url = match.source_url or ""
        used_from_url = per_source.get(url, 0)
        is_protected = bool(match.chunk_id and match.chunk_id in protected)
        if not is_protected and used_from_url >= max_chunks_per_source_url:
            continue

        selected.append(match)
        per_source[url] = used_from_url + 1
        if len(selected) >= max_chunks_returned:
            break

    return selected
