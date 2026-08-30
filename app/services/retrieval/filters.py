"""Build Pinecone metadata filters for knowledge sources."""

from __future__ import annotations

from typing import Any

# User-facing families. `blog` is accepted then aliased to `website`.
ALLOWED_SOURCES = frozenset({"all", "website", "drive", "whatsapp"})
_ALIASES = {"blog": "website"}


def normalize_source(source: str | None) -> str:
    value = (source or "all").strip().lower()
    value = _ALIASES.get(value, value)
    if value not in ALLOWED_SOURCES:
        raise ValueError(f"source must be one of {sorted(ALLOWED_SOURCES)}, got {source!r}")
    return value


def build_source_filter(source: str | None) -> dict[str, Any] | None:
    """
    Return a Pinecone metadata filter for the selected source.

    - all: no filter (website + drive + whatsapp)
    - drive: source_type == drive
    - whatsapp: source_type == whatsapp
    - website (and deprecated blog): public arnifi.com content as one family ΓÇö
      blogs, country overviews, service landings, and package/product details
      (source_type blog or website, or source_domain arnifi.com).
    """
    value = normalize_source(source)
    if value == "all":
        return None
    if value == "drive":
        return {"source_type": {"$eq": "drive"}}
    if value == "whatsapp":
        return {"source_type": {"$eq": "whatsapp"}}
    return {
        "$or": [
            {"source_type": {"$eq": "blog"}},
            {"source_type": {"$eq": "website"}},
            {"source_domain": {"$eq": "arnifi.com"}},
        ]
    }


def empty_result_message(source: str | None = "all") -> str:
    value = normalize_source(source)
    if value == "drive":
        return "I could not find relevant Google Drive documents for that question."
    if value == "website":
        return "I could not find relevant Arnifi website content for that question."
    if value == "whatsapp":
        return "I could not find relevant WhatsApp conversation knowledge for that question."
    return "I could not find relevant Arnifi knowledge for that question."
