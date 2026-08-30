"""Prefer full country-page section sets for list / FAQ style questions."""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlparse

from app.models.schemas import RetrievedChunk
from app.utils.helpers import get_logger

logger = get_logger(__name__)

_LIST_INTENT = re.compile(
    r"(?is)\b("
    r"key\s+selling\s+points?|selling\s+points?|"
    r"faqs?|frequently\s+asked|"
    r"application\s+process|process\s+(flow|steps?)|"
    r"top\s+funds?|explore\s+funds?|"
    r"top\s+packages?|explore\s+packages?|\bpackages?\b|"
    r"compar(?:e|ison|ing)|vs\.?|versus|"
    r"list\s+(all|the|every)|all\s+\d+|country\s+page|"
    r"exactly|verbatim|numbered"
    r")\b"
)

_SECTION_CUES: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (
        re.compile(r"(?is)\b(key\s+selling\s+points?|selling\s+points?)\b"),
        frozenset({"selling_points"}),
    ),
    (
        re.compile(r"(?is)\b(faqs?|frequently\s+asked)\b"),
        frozenset({"faq", "faqs"}),
    ),
    (
        re.compile(r"(?is)\b(application\s+process|process\s+(flow|steps?))\b"),
        frozenset({"process_flow"}),
    ),
    (
        re.compile(r"(?is)\b(top\s+funds?|explore\s+funds?)\b"),
        frozenset({"top_funds", "top_packages"}),
    ),
    (
        re.compile(r"(?is)\b(top\s+packages?|explore\s+packages?|\bpackages?\b)\b"),
        frozenset({"top_packages", "top_funds"}),
    ),
    (
        re.compile(
            r"(?is)\b(compar(?:e|ison|ing)|vs\.?|versus|setup timeline|corporate tax|vat|gst)\b"
        ),
        frozenset({"compare"}),
    ),
)

_ALL_CATALOG_SECTIONS = frozenset(
    {
        "selling_points",
        "faq",
        "faqs",
        "process_flow",
        "top_funds",
        "top_packages",
        "market_insights",
        "overview",
        "compare",
        "introduction",
        "highlights",
        "highlights_and_benefits",
        "terms",
        "terms_and_conditions",
        "documents",
        "pricing",
    }
)

# Question cues ΓåÆ shortcode / name tokens that must appear on the chunk URL or title.
_COUNTRY_CUES: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"(?is)\bhong\s*kong\b|\b/hk\b"), frozenset({"hk", "hong kong", "hong-kong"})),
    (re.compile(r"(?is)\bguernsey\b|\b/gg\b"), frozenset({"gg", "guernsey"})),
    (re.compile(r"(?is)\buae\b|united arab emirates|\b/ae\b"), frozenset({"ae", "uae"})),
    (re.compile(r"(?is)\bsaudi\b|\bksa\b|\b/sa\b"), frozenset({"sa", "saudi"})),
    (re.compile(r"(?is)\bsingapore\b|\b/sg\b"), frozenset({"sg", "singapore"})),
    (re.compile(r"(?is)\bmauritius\b|\b/mu\b"), frozenset({"mu", "mauritius"})),
    (re.compile(r"(?is)\bcyprus\b|\b/cy\b"), frozenset({"cy", "cyprus"})),
    (re.compile(r"(?is)\bireland\b|\b/ie\b"), frozenset({"ie", "ireland"})),
    (re.compile(r"(?is)\bluxembourg\b|\b/lu\b"), frozenset({"lu", "luxembourg"})),
    (re.compile(r"(?is)\bmalaysia\b|\b/my\b"), frozenset({"my", "malaysia"})),
    (re.compile(r"(?is)\bcayman\b|\b/cym\b"), frozenset({"cym", "cayman"})),
    (re.compile(r"(?is)\bbvi\b|british virgin|\b/vg\b"), frozenset({"vg", "virgin", "bvi"})),
    (re.compile(r"(?is)\bpuerto\s*rico\b|\b/pr\b"), frozenset({"pr", "puerto"})),
    (
        re.compile(r"(?is)\bunited\s+kingdom\b|\b\buk\b|\b/gb\b"),
        frozenset({"gb", "uk", "united kingdom", "united-kingdom"}),
    ),
    (
        re.compile(r"(?is)\bsaint\s+vincent|\bst\.?\s*vincent|\b/vc\b"),
        frozenset({"vc", "vincent", "grenadines"}),
    ),
)


def is_catalog_section_list_question(question: str) -> bool:
    """True when the user wants a full list from a location/service page."""
    return bool(_LIST_INTENT.search(question or ""))


def target_catalog_sections(question: str) -> frozenset[str]:
    """Section heading prefixes to prefer for this question."""
    text = question or ""
    # On enriched follow-ups ("Cyprus ΓÇª\\ntop packages"), prefer the last line's cue.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    focus = lines[-1] if lines else text
    hits: set[str] = set()
    for pattern, labels in _SECTION_CUES:
        if pattern.search(focus):
            hits |= set(labels)
    if hits:
        return frozenset(hits)
    for pattern, labels in _SECTION_CUES:
        if pattern.search(text):
            hits |= set(labels)
    if hits:
        return frozenset(hits)
    if is_catalog_section_list_question(text):
        return _ALL_CATALOG_SECTIONS
    return frozenset()


def mentioned_country_tokens(question: str) -> frozenset[str]:
    """Shortcode/name tokens for a country named in the question (empty if none)."""
    text = question or ""
    tokens: set[str] = set()
    for pattern, vals in _COUNTRY_CUES:
        if pattern.search(text):
            tokens |= set(vals)
    return frozenset(tokens)


def chunk_matches_country(chunk: RetrievedChunk, tokens: frozenset[str]) -> bool:
    if not tokens:
        return True
    url = (chunk.source_url or "").lower()
    title = " ".join(
        [
            chunk.doc_title or "",
            str((chunk.metadata or {}).get("doc_title") or ""),
        ]
    ).lower()
    path = urlparse(url).path.lower().strip("/")
    path_parts = {p for p in path.split("/") if p}
    for token in tokens:
        t = token.lower()
        # Shortcodes (ae, cy, hkΓÇª) must match a path segment exactly ΓÇö never a
        # substring (otherwise "cy" matches /cym/ Cayman).
        if len(t) <= 3 and t.isalpha():
            if t in path_parts or path == t:
                return True
            continue
        if t in title or t in url or t in path_parts:
            return True
    return False


def _page_kind(chunk: RetrievedChunk) -> str:
    meta = chunk.metadata or {}
    return str(meta.get("page_kind") or "").strip().lower()


def _heading_root(chunk: RetrievedChunk) -> str:
    path = (chunk.heading_path or "").strip()
    if not path:
        meta = chunk.metadata or {}
        path = str(meta.get("heading_path") or "")
    return path.split(">", 1)[0].strip().lower()


def is_catalog_section_chunk(
    chunk: RetrievedChunk,
    *,
    sections: Iterable[str] | None = None,
    country_tokens: frozenset[str] | None = None,
) -> bool:
    """Country/catalog section chunk (optionally filtered by heading root + country)."""
    if _page_kind(chunk) not in {
        "country_overview",
        "service_landing",
        "service_package",
        "product_detail",
    }:
        if str((chunk.metadata or {}).get("source_type") or "").lower() not in {
            "website",
            "",
        }:
            return False
    root = _heading_root(chunk)
    allowed = frozenset(sections) if sections is not None else _ALL_CATALOG_SECTIONS
    if root not in allowed:
        return False
    if country_tokens is not None and not chunk_matches_country(chunk, country_tokens):
        return False
    return True


def apply_catalog_section_boost(
    question: str,
    matches: list[RetrievedChunk],
    *,
    boost: float = 0.18,
) -> list[RetrievedChunk]:
    """
    Rank matching country-page section chunks above blogs/overview paraphrases.

    Re-sorts by boosted score (descending). Non-matching order among ties is stable.
    For compare questions, prefer full-table / per-peer summary chunks over
    tiny per-metric chips so all named countries stay in context.
    """
    if not matches or not is_catalog_section_list_question(question):
        return matches

    sections = target_catalog_sections(question)
    countries = mentioned_country_tokens(question)
    compare_q = "compare" in sections
    boosted: list[RetrievedChunk] = []
    hit = 0
    for chunk in matches:
        score = float(chunk.score or 0.0)
        if is_catalog_section_chunk(
            chunk, sections=sections, country_tokens=countries or None
        ):
            score = min(0.99, score + boost)
            hit += 1
            meta = dict(chunk.metadata or {})
            meta["catalog_section_boost"] = True
            if compare_q:
                root = _heading_root(chunk)
                path = (chunk.heading_path or "").strip()
                depth = path.count(">")
                # Full table (path == compare) or peer summary (compare > Country)
                if root == "compare" and depth == 0:
                    score = min(0.99, score + 0.35)
                    meta["compare_table_boost"] = True
                elif root == "compare" and depth == 1:
                    score = min(0.99, score + 0.28)
                    meta["compare_peer_boost"] = True
                elif root == "compare" and depth >= 2:
                    score = max(0.01, score - 0.08)
                    meta["compare_field_demote"] = True
            chunk = chunk.model_copy(update={"score": score, "metadata": meta})
        boosted.append(chunk)

    if hit:
        logger.info(
            "Catalog section boost: raised %d chunk(s) for sections=%s countries=%s",
            hit,
            sorted(sections),
            sorted(countries) if countries else ["*"],
        )
        boosted.sort(key=lambda c: float(c.score or 0.0), reverse=True)
    return boosted


def protected_chunk_ids_for_catalog_sections(
    question: str, matches: list[RetrievedChunk]
) -> set[str]:
    """Bypass per-URL diversify cap so all FAQ / selling-point rows can survive."""
    if not is_catalog_section_list_question(question):
        return set()
    sections = target_catalog_sections(question)
    countries = mentioned_country_tokens(question)
    protected: set[str] = set()
    for chunk in matches:
        if chunk.chunk_id and is_catalog_section_chunk(
            chunk, sections=sections, country_tokens=countries or None
        ):
            protected.add(chunk.chunk_id)
    if protected:
        logger.info(
            "Catalog section protect: %d chunk(s) bypass URL cap",
            len(protected),
        )
    return protected


def filter_cross_country_catalog_noise(
    question: str, matches: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """
    When the question names a country and asks for a section list, drop other
    countries' same section-type chunks so they do not fill the context window.
    """
    if not matches or not is_catalog_section_list_question(question):
        return matches
    countries = mentioned_country_tokens(question)
    if not countries:
        return matches
    sections = target_catalog_sections(question)
    kept: list[RetrievedChunk] = []
    dropped = 0
    for chunk in matches:
        if is_catalog_section_chunk(chunk, sections=sections) and not chunk_matches_country(
            chunk, countries
        ):
            dropped += 1
            continue
        kept.append(chunk)
    if dropped:
        logger.info(
            "Catalog country filter: dropped %d off-country section chunk(s)",
            dropped,
        )
        return kept
    return matches


def catalog_list_limits(
    question: str,
    *,
    default_returned: int,
    default_per_url: int,
    list_returned: int,
    list_per_url: int,
    list_top_k: int,
    default_top_k: int,
) -> tuple[int, int, int]:
    """Return (max_chunks_returned, max_per_url, top_k_initial)."""
    if is_catalog_section_list_question(question):
        return list_returned, list_per_url, list_top_k
    return default_returned, default_per_url, default_top_k
