"""
Online query pipeline — embed → Pinecone → diversify → LLM.

Depends on Embedder / VectorStore / LLM protocols, not concrete AWS SDKs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from app.models.schemas import RAGResponse, RetrievedChunk
from app.services.retrieval.diversify import diversify_chunks
from app.services.retrieval.relevance_gate import apply_relevance_gate
from app.services.retrieval.fee_focus import (
    apply_fee_focus,
    apply_service_code_boost,
    apply_title_boost,
    extract_service_codes,
    prefer_title_matches_for_fees,
    protected_chunk_ids_for_codes,
    protected_chunk_ids_for_titles,
)
from app.services.retrieval.filters import build_source_filter, empty_result_message
from app.services.retrieval.service_code_lookup import (
    lookup_service_code_chunks,
    lookup_title_chunks,
    merge_lexical_service_code_hits,
    merge_lexical_title_hits,
)
from app.utils.whatsapp_open import enrich_whatsapp_sources
from app.services.retrieval.source_intent import resolve_source_intent
from app.utils.helpers import get_logger

if TYPE_CHECKING:
    from app.config.settings import Settings

logger = get_logger(__name__)

HistoryTurn = dict[str, str]

# Only these look like "continue prior topic" — not a new standalone question.
# "What about IQAMA Medical fees?" must NOT match (new topic in the same chat).
_ELLIPTICAL_FOLLOW_UP = re.compile(
    r"(?is)^\s*(?:"
    r"(?:what|how)\s+about\s+(?:the\s+)?(?:cost|fees?|price|pricing|documents?|"
    r"requirements?|timeline|process|that|this|it)\??|"
    r"and\s+(?:the\s+)?(?:cost|fees?|price|documents?|requirements?|that|this|it)\??|"
    r"how\s+much(?:\s+(?:is\s+it|are\s+they|does\s+it\s+cost|for\s+(?:that|this|it)))?\??|"
    r"(?:the\s+)?(?:cost|fees?|price|documents?(?:\s+required)?|requirements?|timeline)\??|"
    r"(?:and\s+)?(?:from\s+)?(?:whatsapp|blog|website|drive|the\s+website)\??"
    r")\s*$"
)


def _normalize_history(history: list[HistoryTurn] | None) -> list[HistoryTurn]:
    if not history:
        return []
    cleaned: list[HistoryTurn] = []
    for turn in history[-8:]:
        role = (turn.get("role") or "").strip().lower()
        content = (turn.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        cleaned.append({"role": role, "content": content[:2000]})
    return cleaned


def is_elliptical_follow_up(question: str) -> bool:
    """True when the question depends on prior topic (e.g. 'what about the cost?')."""
    q = (question or "").strip()
    if not q:
        return False
    return bool(_ELLIPTICAL_FOLLOW_UP.match(q))


def build_search_text(question: str, history: list[HistoryTurn] | None = None) -> str:
    """
    Embed text for Pinecone.

    Follow-ups like "what about the cost?" need the prior user topic.
    Standalone questions in the same chat must NOT prepend old topics — that
    pollutes retrieval (e.g. IQAMA fees after a Malaysia VAT question) and
    makes the model say the answer is missing from context.
    """
    q = question.strip()
    turns = _normalize_history(history)
    prior_users = [t["content"] for t in turns if t["role"] == "user"]
    if not prior_users:
        return q
    if not is_elliptical_follow_up(q):
        return q
    # Most recent user turn is the active topic (not the first message in the thread).
    topic = prior_users[-1][:300].strip()
    if not topic or topic.lower() == q.lower():
        return q
    return f"{topic}\n{q}"


def history_for_generation(
    question: str, history: list[HistoryTurn] | None = None
) -> list[HistoryTurn] | None:
    """
    History for the answer LLM.

    Same rule as retrieval enrichment: only keep prior turns for true
    elliptical follow-ups. On a topic switch (new standalone question in the
    same chat), injecting Malaysia VAT history while Context is IQAMA pricing
    makes Nova hedge with "not in context" even when the right chunks are present.
    """
    turns = _normalize_history(history)
    if not turns:
        return None
    if is_elliptical_follow_up(question):
        return turns
    return None


class QueryEngine:
    """Answer questions using indexed blog and/or Drive knowledge."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _enrich_sources(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach WhatsApp group invite links from the sync registry."""
        try:
            return enrich_whatsapp_sources(
                sources,
                get_invite_link=self.settings.state_db.get_wa_invite_link,
            )
        except Exception as exc:
            logger.warning("WhatsApp source enrichment skipped: %s", exc)
            return sources

    def ask(
        self,
        question: str,
        source: str = "all",
        history: list[HistoryTurn] | None = None,
    ) -> RAGResponse:
        turns = _normalize_history(history)
        resolved = resolve_source_intent(
            question, client_source=source, history=turns
        )
        if resolved != (source or "all").strip().lower():
            logger.info(
                "Source intent auto-detect: client=%s → resolved=%s",
                source,
                resolved,
            )
        selected = self._retrieve(question, source=resolved, history=turns)
        if not selected:
            return RAGResponse(
                answer=empty_result_message(resolved),
                sources=[],
            )
        gen_history = history_for_generation(question, turns)
        response = self.settings.generator.generate(
            question, selected, history=gen_history
        )
        return RAGResponse(
            answer=response.answer,
            sources=self._enrich_sources(list(response.sources or [])),
        )

    def inspect_retrieve(
        self,
        question: str,
        source: str = "all",
        history: list[HistoryTurn] | None = None,
    ) -> list[RetrievedChunk]:
        """
        Public retrieve-only path for eval / debugging.

        Applies the same source-intent resolution as ask(), then the same
        embed → Pinecone → diversify pipeline as production.
        """
        turns = _normalize_history(history)
        resolved = resolve_source_intent(
            question, client_source=source, history=turns
        )
        return self._retrieve(question, source=resolved, history=turns)

    def _retrieve(
        self,
        question: str,
        source: str = "all",
        history: list[HistoryTurn] | None = None,
    ) -> list[RetrievedChunk]:
        search_text = build_search_text(question, history)
        if search_text != question.strip():
            logger.info("Follow-up retrieval query enriched with history topic")
        query_vector = self.settings.embedder.embed_query(search_text)
        top_k = self.settings.setting("retrieval", "top_k_initial", default=20)
        source_filter = build_source_filter(source)
        matches = self.settings.vectorstore.query(
            query_vector,
            top_k=top_k,
            filter=source_filter,
        )
        if not matches and source_filter is not None:
            logger.info(
                "Empty Pinecone results for source=%s; falling back to all sources",
                source,
            )
            matches = self.settings.vectorstore.query(
                query_vector,
                top_k=top_k,
                filter=None,
            )

        # Titan often misses rare PM#### IDs / confuses near-twin Titles —
        # inject exact pricing rows from bundled index (Lambda) or local extracts.
        artifacts_dir = self.settings.artifacts.base_dir
        embedding_model = str(
            self.settings.setting(
                "embedding", "model", default="amazon.titan-embed-text-v2:0"
            )
        )
        bundled_raw = self.settings.setting(
            "retrieval", "pricing_lexical_index", default=None
        )
        bundled_index_path = None
        if bundled_raw:
            candidate = Path(str(bundled_raw))
            if not candidate.is_absolute():
                candidate = self.settings.config_path.parent.parent / candidate
            bundled_index_path = candidate

        if extract_service_codes(question):
            lexical = lookup_service_code_chunks(
                question,
                artifacts_dir=artifacts_dir,
                embedding_model=embedding_model,
                bundled_index_path=bundled_index_path,
            )
            if lexical:
                logger.info(
                    "Lexical service-code rescue: %d row(s) for %s",
                    len(lexical),
                    sorted(extract_service_codes(question)),
                )
            matches = merge_lexical_service_code_hits(question, matches, lexical)

        title_hits = lookup_title_chunks(
            question,
            artifacts_dir=artifacts_dir,
            embedding_model=embedding_model,
            bundled_index_path=bundled_index_path,
        )
        if title_hits:
            logger.info(
                "Lexical title rescue: %d row(s)",
                len(title_hits),
            )
            matches = merge_lexical_title_hits(question, matches, title_hits)

        focused = apply_fee_focus(question, matches)
        boosted = apply_service_code_boost(question, focused)
        boosted = prefer_title_matches_for_fees(question, boosted)
        boosted = apply_title_boost(question, boosted)
        min_score = float(
            self.settings.setting("retrieval", "min_score", default=0.45)
        )
        gated = apply_relevance_gate(
            boosted, question=question, min_score=min_score
        )
        if not gated:
            return []
        bypass_cap = bool(
            self.settings.setting(
                "retrieval",
                "exact_service_code_bypasses_url_cap",
                default=True,
            )
        )
        protected: set[str] = set()
        if bypass_cap:
            protected |= protected_chunk_ids_for_codes(question, gated)
            protected |= protected_chunk_ids_for_titles(question, gated)
        return diversify_chunks(
            gated,
            max_chunks_returned=self.settings.setting(
                "retrieval", "max_chunks_returned", default=5
            ),
            max_chunks_per_source_url=self.settings.setting(
                "retrieval", "max_chunks_per_source_url", default=2
            ),
            protected_chunk_ids=protected or None,
        )

    def ask_stream(
        self,
        question: str,
        source: str = "all",
        history: list[HistoryTurn] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Same retrieval as ask(), but stream the LLM answer token-by-token."""
        turns = _normalize_history(history)
        resolved = resolve_source_intent(
            question, client_source=source, history=turns
        )
        if resolved != (source or "all").strip().lower():
            logger.info(
                "Source intent auto-detect: client=%s → resolved=%s",
                source,
                resolved,
            )
        selected = self._retrieve(question, source=resolved, history=turns)
        if not selected:
            yield {"type": "sources", "sources": []}
            yield {
                "type": "token",
                "content": empty_result_message(resolved),
            }
            yield {"type": "done"}
            return

        gen_history = history_for_generation(question, turns)
        _, sources = self.settings.generator.build_prompt(
            question, selected, history=gen_history
        )
        yield {"type": "sources", "sources": self._enrich_sources(sources)}

        answer_parts: list[str] = []
        for token in self.settings.generator.generate_stream(
            question, selected, history=gen_history
        ):
            answer_parts.append(token)
            yield {"type": "token", "content": token}

        # Stop the "typing" UI first; related chips can arrive a moment later.
        yield {"type": "done"}

        suggest = getattr(self.settings.generator, "suggest_related_questions", None)
        if callable(suggest):
            try:
                related = suggest(question, "".join(answer_parts))
                if related:
                    yield {"type": "related", "questions": related}
            except Exception as exc:
                logger.warning("Skipping related questions: %s", exc)

    def inspect(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.settings.state_db.stats()}
        try:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        except Exception as exc:
            result["index_stats_error"] = str(exc)
        return result
