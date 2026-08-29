"""
Programmatic RAG eval metrics (no LLM judge).

hit_at_k — did any of the top-k chunks "count" as relevant for this case?
context_recall_proxy — what fraction of must-include needles appear in retrieved text?
answer_must_include_rate — same needles checked against the generated answer (cheap gen signal).
"""

from __future__ import annotations

from typing import Any, Sequence


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if str(v).strip()]


def url_is_match(chunk_url: str, references: Sequence[str]) -> bool:
    """True if chunk_url equals a reference or starts with a reference prefix."""
    url = (chunk_url or "").strip()
    if not url:
        return False
    for ref in references:
        needle = (ref or "").strip()
        if not needle:
            continue
        if url == needle or url.startswith(needle):
            return True
    return False


def text_contains_any(text: str, needles: Sequence[str]) -> bool:
    blob = text or ""
    return any(n and n in blob for n in needles)


def chunk_is_hit(
    chunk: Any,
    *,
    reference_source_urls: Sequence[str],
    reference_must_include: Sequence[str],
) -> bool:
    """
    A chunk is a hit if its source_url matches a reference URL/prefix
    OR its chunk_text contains any must-include substring.
    """
    urls = _as_str_list(reference_source_urls)
    needles = _as_str_list(reference_must_include)
    source_url = getattr(chunk, "source_url", None)
    if source_url is None and isinstance(chunk, dict):
        source_url = chunk.get("source_url", "")
    chunk_text = getattr(chunk, "chunk_text", None)
    if chunk_text is None and isinstance(chunk, dict):
        chunk_text = chunk.get("chunk_text", "")

    if urls and url_is_match(str(source_url or ""), urls):
        return True
    if needles and text_contains_any(str(chunk_text or ""), needles):
        return True
    return False


def hit_at_k(
    chunks: Sequence[Any],
    *,
    reference_source_urls: Sequence[str] | None = None,
    reference_must_include: Sequence[str] | None = None,
) -> float | None:
    """
    1.0 if any retrieved chunk is a hit, else 0.0.

    Returns None when the case has no URL refs and no must-include strings
    (typical for pure negative / hedge cases) so means skip those cases.
    """
    urls = _as_str_list(reference_source_urls)
    needles = _as_str_list(reference_must_include)
    if not urls and not needles:
        return None
    if any(
        chunk_is_hit(
            c,
            reference_source_urls=urls,
            reference_must_include=needles,
        )
        for c in chunks
    ):
        return 1.0
    return 0.0


def context_recall_proxy(
    chunks: Sequence[Any],
    *,
    reference_must_include: Sequence[str] | None = None,
) -> float | None:
    """
    Fraction of must-include strings found anywhere in the retrieved chunk texts.

    Returns None when there are no must-include needles.
    """
    needles = _as_str_list(reference_must_include)
    if not needles:
        return None
    parts: list[str] = []
    for c in chunks:
        text = getattr(c, "chunk_text", None)
        if text is None and isinstance(c, dict):
            text = c.get("chunk_text", "")
        parts.append(str(text or ""))
    blob = "\n".join(parts)
    found = sum(1 for n in needles if n in blob)
    return found / len(needles)


def answer_must_include_rate(
    answer: str,
    *,
    reference_must_include: Sequence[str] | None = None,
) -> float | None:
    """Fraction of must-include strings that appear in the generated answer."""
    needles = _as_str_list(reference_must_include)
    if not needles:
        return None
    blob = answer or ""
    found = sum(1 for n in needles if n in blob)
    return found / len(needles)


def answer_forbid_ok(
    answer: str,
    *,
    answer_must_not_include: Sequence[str] | None = None,
) -> float | None:
    """
    1.0 if none of the forbidden substrings appear in the answer, else 0.0.

    Returns None when there is no forbid list.
    """
    forbidden = _as_str_list(answer_must_not_include)
    if not forbidden:
        return None
    blob = answer or ""
    if any(n and n in blob for n in forbidden):
        return 0.0
    return 1.0


def mean_skip_none(values: Sequence[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)
