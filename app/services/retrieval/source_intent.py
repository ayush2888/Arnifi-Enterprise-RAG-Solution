"""Detect which knowledge source the user asked for from question text.

Only used when the client sends source="all". Explicit client filters win.
"""

from __future__ import annotations

import re

from app.services.retrieval.filters import ALLOWED_SOURCES, normalize_source

HistoryTurn = dict[str, str]

# Clear exclusive signals (safe to match anywhere in the question).
_WHATSAPP = re.compile(
    r"\b(?:whatsapp|whats\s*app|periskope|wa\s*chats?|wa\s*groups?)\b",
    re.IGNORECASE,
)
_WEBSITE = re.compile(
    r"\b(?:blogs?|blog\s*posts?|articles?|arnifi\.com)\b"
    r"|\b(?:from|on|in|check(?:\s+the)?|using|via)\s+(?:the\s+)?(?:website|site)\b"
    # Catalog / package pages are the same website family as blogs
    r"|\b(?:product[\s-]?details?|service\s+pages?|package\s+pages?|"
    r"country\s+pages?|business\s+setup|micro[\s-]?services?)\b"
    r"|\b(?:from|on|in)\s+(?:the\s+)?(?:catalog|services?|packages?)\b",
    re.IGNORECASE,
)
# "documents required for KYC" must NOT count as Drive — only explicit Drive cues.
# Softened: generic "docs/pdfs/files" alone no longer locks source=drive (empty Drive index risk).
_DRIVE = re.compile(
    r"\bgoogle\s*drive\b"
    r"|\b(?:from|in|on|check(?:\s+the)?|using|via|search)\s+"
    r"(?:the\s+|our\s+|my\s+)?(?:google\s*)?drive\b"
    r"|\bdrive\s+(?:docs?|documents?|files?|pdfs?)\b",
    re.IGNORECASE,
)

# User explicitly wants mixed / unrestricted retrieval.
_FORCE_ALL = re.compile(
    r"\b(?:all\s+sources|everything|both|as\s+well|also\s+from|"
    r"blogs?\s+too|website\s+too|drive\s+too|whatsapp\s+too)\b",
    re.IGNORECASE,
)

_SOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("whatsapp", _WHATSAPP),
    ("website", _WEBSITE),
    ("drive", _DRIVE),
)


def _intent_text(question: str, history: list[HistoryTurn] | None = None) -> str:
    """
    Prefer the current question for intent.

    Ultra-short follow-ups like "and WhatsApp?" still work from current text alone.
    If the current question is very short and has no source cue, peek at the
    latest prior user turn (not the whole thread).
    """
    q = (question or "").strip()
    if not q:
        return ""
    if len(q) >= 12:
        return q
    for name, pattern in _SOURCE_PATTERNS:
        if pattern.search(q):
            return q
    if not history:
        return q
    for turn in reversed(history):
        if (turn.get("role") or "").strip().lower() != "user":
            continue
        prior = (turn.get("content") or "").strip()
        if prior:
            return f"{prior}\n{q}"
    return q


def detect_source_families(text: str) -> set[str]:
    """Return which source families are mentioned in text."""
    hits: set[str] = set()
    for name, pattern in _SOURCE_PATTERNS:
        if pattern.search(text or ""):
            hits.add(name)
    return hits


def resolve_source_intent(
    question: str,
    *,
    client_source: str | None = "all",
    history: list[HistoryTurn] | None = None,
) -> str:
    """
    Resolve the Pinecone source filter label.

    - Explicit client source (website/drive/whatsapp; blog aliases to website) always wins.
    - When client says all, detect intent from the question.
    - One family → that source; two or more / force-all phrases → all.
    - No clear cue → all.
    """
    client = normalize_source(client_source)
    if client != "all":
        return client

    text = _intent_text(question, history)
    if not text:
        return "all"

    if _FORCE_ALL.search(text):
        return "all"

    hits = detect_source_families(text)
    if len(hits) == 1:
        only = next(iter(hits))
        assert only in ALLOWED_SOURCES
        return only
    # 0 hits or 2+ hits ("docs and WhatsApp") → search broadly
    return "all"
