"""
Prefer Pricing Master fee rows over payment-gateway noise for fee/payer questions.

Problem: queries like "who handles payment for IQAMA Authority fee?" match
generic Razorpay / payment-gateway PDFs. Those chunks leak into answers even
when Pricing rows already answer the fee amounts and leave payer unknown.

Also: exact PM#### boost — Titan often ranks neighbor SKUs (PM1121/PM1123)
above the typed code (PM1122). Promote literal service-code matches first.
"""

from __future__ import annotations

import re

from app.models.schemas import RetrievedChunk

# Fee amounts, service codes, or explicit payer / "handles payment" questions.
_FEE_OR_PAYER_QUERY = re.compile(
    r"\b(?:"
    r"fee|fees|pricing|price|cost|charges?|tariff|"
    r"authority\s+fee|arnifi\s+fee|service\s+code|"
    r"who\s+pays|who\s+remits|handles?\s+payment|payment\s+for|"
    r"payer|pm\d{3,}"
    r")\b",
    re.IGNORECASE,
)

# "PM1122", "pm 1122", "PM-1122" → normalize to PM1122
_SERVICE_CODE_IN_QUERY = re.compile(r"\bPM\s*-?\s*(\d{3,})\b", re.IGNORECASE)
_SERVICE_CODE_IN_TEXT = re.compile(r"\bPM\s*-?\s*(\d{3,})\b", re.IGNORECASE)

_PRICING_TITLE = re.compile(r"pricing\s*master", re.IGNORECASE)
_PRICING_ROW = re.compile(
    r"(?:authority\s+fees?|arnifi\s+fees?|service\s+code|pm\s*id\s*:|pm\d{3,})",
    re.IGNORECASE,
)
_GATEWAY_NOISE = re.compile(
    r"\b(?:"
    r"razorpay|payment\s+gateway|stripe|paypal|"
    r"online\s+payment\s+gateway\s+service"
    r")\b",
    re.IGNORECASE,
)

# Pricing row "Title: ..." — exact service-name match against the question.
_ROW_TITLE = re.compile(r"(?im)^Title:\s*(.+)$")
_MIN_TITLE_MATCH_LEN = 20


def _blob(chunk: RetrievedChunk) -> str:
    return " ".join(
        [
            chunk.doc_title or "",
            chunk.heading_path or "",
            chunk.chunk_text or "",
        ]
    )


def extract_service_codes(question: str) -> set[str]:
    """Normalize service codes mentioned in the user question (e.g. PM1122)."""
    return {
        f"PM{m.group(1)}" for m in _SERVICE_CODE_IN_QUERY.finditer(question or "")
    }


def chunk_service_codes(chunk: RetrievedChunk) -> set[str]:
    """Service codes present in a retrieved chunk's title/text."""
    blob = _blob(chunk)
    return {f"PM{m.group(1)}" for m in _SERVICE_CODE_IN_TEXT.finditer(blob)}


def chunk_matches_service_codes(
    chunk: RetrievedChunk, codes: set[str]
) -> bool:
    if not codes:
        return False
    return bool(chunk_service_codes(chunk) & codes)


def pricing_row_title(chunk: RetrievedChunk | str) -> str:
    """Extract the Title: field from a pricing row chunk/text."""
    text = chunk if isinstance(chunk, str) else (chunk.chunk_text or "")
    m = _ROW_TITLE.search(text)
    return (m.group(1) if m else "").strip()


def chunk_title_matches_question(
    question: str,
    chunk: RetrievedChunk,
    *,
    min_len: int = _MIN_TITLE_MATCH_LEN,
) -> bool:
    """
    True when the row's full Title appears as a contiguous phrase in the question.

    Distinguishes near-twins, e.g. question mentions
    "Malaysia Corporate Compliance and Tax Services" → match that row,
    not "Malaysia Corporate Tax and SST Compliance Services".
    """
    title = pricing_row_title(chunk)
    if len(title) < min_len:
        return False
    return title.casefold() in (question or "").casefold()


def is_fee_or_payer_query(question: str) -> bool:
    return bool(_FEE_OR_PAYER_QUERY.search(question or ""))


def is_pricing_row_chunk(chunk: RetrievedChunk) -> bool:
    title = chunk.doc_title or ""
    heading = chunk.heading_path or ""
    text = chunk.chunk_text or ""
    if _PRICING_TITLE.search(title) or _PRICING_TITLE.search(heading):
        return True
    return bool(_PRICING_ROW.search(text))


def is_payment_gateway_noise(chunk: RetrievedChunk) -> bool:
    """Gateway/vendor docs that are not themselves Pricing fee rows."""
    if is_pricing_row_chunk(chunk):
        return False
    return bool(_GATEWAY_NOISE.search(_blob(chunk)))


def apply_fee_focus(
    question: str,
    matches: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """
    For fee/payer questions:
    1. Drop payment-gateway noise chunks (e.g. Razorpay board resolutions).
    2. Stable-sort so Pricing Master / fee-row chunks rank above others.
    """
    if not matches or not is_fee_or_payer_query(question):
        return matches

    filtered = [m for m in matches if not is_payment_gateway_noise(m)]
    # If filtering wiped everything (unlikely), keep originals.
    pool = filtered if filtered else list(matches)

    # Higher score first within each tier; pricing tier first.
    return sorted(
        pool,
        key=lambda m: (0 if is_pricing_row_chunk(m) else 1, -float(m.score or 0.0)),
    )


def apply_service_code_boost(
    question: str,
    matches: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """
    Promote chunks that literally contain a PM#### code from the question.

    Runs after fee_focus. Preserves prior relative order among non-matches
    and among matches (stable via original index).
    """
    codes = extract_service_codes(question)
    if not matches or not codes:
        return matches

    indexed = list(enumerate(matches))
    indexed.sort(
        key=lambda pair: (
            0 if chunk_matches_service_codes(pair[1], codes) else 1,
            pair[0],
        )
    )
    return [m for _, m in indexed]


def protected_chunk_ids_for_codes(
    question: str,
    matches: list[RetrievedChunk],
) -> set[str]:
    """Chunk ids that must survive diversify's per-URL cap (exact code hits)."""
    codes = extract_service_codes(question)
    if not codes:
        return set()
    return {
        m.chunk_id
        for m in matches
        if m.chunk_id and chunk_matches_service_codes(m, codes)
    }


def apply_title_boost(
    question: str,
    matches: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """
    Promote pricing rows whose Title: is a contiguous phrase in the question.

    Among title hits, longer titles rank first (more specific service name).
    """
    if not matches:
        return matches
    if not any(chunk_title_matches_question(question, m) for m in matches):
        return matches

    indexed = list(enumerate(matches))

    def sort_key(pair: tuple[int, RetrievedChunk]) -> tuple[int, int, int]:
        idx, chunk = pair
        if chunk_title_matches_question(question, chunk):
            # Tier 0, longer title first, then original index.
            return (0, -len(pricing_row_title(chunk)), idx)
        return (1, 0, idx)

    indexed.sort(key=sort_key)
    return [m for _, m in indexed]


def prefer_title_matches_for_fees(
    question: str,
    matches: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """
    For fee questions with an exact Title hit, keep only those rows so Nova
    cannot quote a near-twin service's fee (699 vs 6,869).
    """
    if not matches or not is_fee_or_payer_query(question):
        return apply_title_boost(question, matches)
    hits = [m for m in matches if chunk_title_matches_question(question, m)]
    if not hits:
        return matches
    hits.sort(key=lambda m: -len(pricing_row_title(m)))
    return hits


def protected_chunk_ids_for_titles(
    question: str,
    matches: list[RetrievedChunk],
) -> set[str]:
    """Exact Title: phrase hits bypass diversify's per-URL cap."""
    return {
        m.chunk_id
        for m in matches
        if m.chunk_id and chunk_title_matches_question(question, m)
    }
