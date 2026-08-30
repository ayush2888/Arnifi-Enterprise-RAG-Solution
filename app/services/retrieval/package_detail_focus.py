"""Prefer website package-detail chunks over Drive Pricing Master for SKU summaries."""

from __future__ import annotations

import re

from app.models.schemas import RetrievedChunk
from app.services.retrieval.fee_focus import is_pricing_row_chunk
from app.utils.helpers import get_logger

logger = get_logger(__name__)

# Explicit package/SKU summary intent (country eval + portal wording).
_PACKAGE_DETAIL_INTENT = re.compile(
    r"(?is)\b("
    r"package\s+detail(?:\s+page)?|"
    r"product\s+detail(?:\s+page)?|"
    r"summarize\s+the\s+introduction|"
    r"introduction,\s*highlights|"
    r"highlights\s*/\s*benefits|"
    r"process\s+flow,\s*faqs?|"
    # Document-list questions must use website Documents Required, not Pricing Master
    r"documents?\s+required|"
    r"required\s+documents?|"
    r"what\s+documents?\b|"
    r"which\s+documents?\b|"
    r"docs?\s+needed|"
    r"documents?\s+needed|"
    r"supporting\s+documents?"
    r")\b"
)

_PRODUCT_DETAIL_SECTIONS = frozenset(
    {
        "introduction",
        "highlights_and_benefits",
        "highlights",
        "process_flow",
        "faqs",
        "faq",
        "terms_and_conditions",
        "terms",
        "pricing",
        "documents",
        "documents_required",
        "documents_from_authority",
    }
)

_DOCUMENTS_SECTION = frozenset(
    {
        "documents_required",
        "documents",
        "documents_from_authority",
        "more_about_required_documents",
    }
)

_DOCUMENTS_QUERY = re.compile(
    r"(?is)\b("
    r"documents?\s+required|"
    r"required\s+documents?|"
    r"what\s+documents?\b|"
    r"which\s+documents?\b|"
    r"docs?\s+needed|"
    r"documents?\s+needed|"
    r"supporting\s+documents?"
    r")\b"
)


def is_package_detail_query(question: str) -> bool:
    """True when the user wants a named package's intro/highlights/process/FAQ/T&C."""
    return bool(_PACKAGE_DETAIL_INTENT.search(question or ""))


def is_documents_required_query(question: str) -> bool:
    """True when the user is asking for the Documents Required list."""
    return bool(_DOCUMENTS_QUERY.search(question or ""))


def _page_kind(chunk: RetrievedChunk) -> str:
    return str((chunk.metadata or {}).get("page_kind") or "").strip().lower()


def _section_root(chunk: RetrievedChunk) -> str:
    meta = chunk.metadata or {}
    catalog = str(meta.get("catalog_section") or "").strip().lower()
    if catalog:
        return catalog.split(">", 1)[0].strip()
    path = (chunk.heading_path or str(meta.get("heading_path") or "")).strip()
    return path.split(">", 1)[0].strip().lower()


def is_product_detail_chunk(chunk: RetrievedChunk) -> bool:
    if _page_kind(chunk) == "product_detail":
        return True
    url = (chunk.source_url or "").lower()
    return "/product-details/" in url


def apply_package_detail_focus(
    question: str,
    matches: list[RetrievedChunk],
    *,
    boost: float = 0.22,
) -> list[RetrievedChunk]:
    """
    For package-detail summary queries:
    1. Drop / demote Drive Pricing Master rows (fee_focus otherwise promotes them
       because the prompt says "Include pricing if shown").
    2. Boost page_kind=product_detail website chunks.
    3. For documents-required questions, prefer documents_required sections.
    """
    if not matches or not is_package_detail_query(question):
        return matches

    docs_q = is_documents_required_query(question)
    kept: list[RetrievedChunk] = []
    dropped = 0
    for chunk in matches:
        if is_pricing_row_chunk(chunk) and not is_product_detail_chunk(chunk):
            dropped += 1
            continue
        score = float(chunk.score or 0.0)
        meta = dict(chunk.metadata or {})
        if is_product_detail_chunk(chunk):
            score = min(0.99, score + boost)
            meta["package_detail_boost"] = True
            section = _section_root(chunk)
            if docs_q and section in _DOCUMENTS_SECTION:
                score = min(0.99, score + 0.18)
                meta["documents_required_boost"] = True
            elif docs_q and section in {"faqs", "faq"}:
                # FAQ often has a vague "what documents" answer ΓÇö demote vs real list
                score = max(0.01, score - 0.12)
                meta["documents_faq_demote"] = True
            chunk = chunk.model_copy(update={"score": score, "metadata": meta})
        kept.append(chunk)

    pool = kept if kept else list(matches)
    pool.sort(key=lambda c: float(c.score or 0.0), reverse=True)
    if dropped:
        logger.info(
            "Package-detail focus: dropped %d pricing-sheet chunk(s), kept %d",
            dropped,
            len(pool),
        )
    return pool


def diversify_package_detail_sections(
    question: str,
    matches: list[RetrievedChunk],
    *,
    max_chunks: int = 12,
) -> list[RetrievedChunk]:
    """
    Prefer one chunk per (package URL, section root) so a summary query gets
    introduction + highlights + process + faqs + terms ΓÇö not five FAQ rows.
    """
    if not matches or not is_package_detail_query(question):
        return matches

    primary: list[RetrievedChunk] = []
    other_packages: list[RetrievedChunk] = []
    duplicate_sections: list[RetrievedChunk] = []
    seen_section: set[tuple[str, str]] = set()

    for chunk in matches:
        if not is_product_detail_chunk(chunk):
            other_packages.append(chunk)
            continue
        root = _section_root(chunk)
        if root not in _PRODUCT_DETAIL_SECTIONS:
            other_packages.append(chunk)
            continue
        key = ((chunk.source_url or "").rstrip("/").lower(), root)
        if key in seen_section:
            duplicate_sections.append(chunk)
            continue
        seen_section.add(key)
        primary.append(chunk)

    # Prefer unique sections; drop same-URL/section duplicates (FAQ spam).
    ordered = primary + other_packages
    if len(primary) >= 2:
        logger.info(
            "Package-detail section diversify: %d unique section(s) across packages "
            "(suppressed %d duplicate-section chunk(s))",
            len(primary),
            len(duplicate_sections),
        )
    return ordered[:max_chunks]


def package_detail_limits(
    question: str,
    *,
    default_returned: int,
    default_per_url: int,
    default_top_k: int,
) -> tuple[int, int, int]:
    """Widen context for multi-section package summaries."""
    if not is_package_detail_query(question):
        return default_returned, default_per_url, default_top_k
    return max(default_returned, 12), max(default_per_url, 8), max(default_top_k, 40)
