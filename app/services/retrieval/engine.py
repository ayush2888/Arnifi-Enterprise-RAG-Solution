"""
Online query pipeline — embed → Pinecone → diversify → LLM.

Depends on Embedder / VectorStore / LLM protocols, not concrete AWS SDKs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

from app.models.schemas import RAGResponse, RetrievedChunk
from app.services.retrieval.diversify import diversify_chunks
from app.utils.helpers import get_logger

if TYPE_CHECKING:
    from app.config.settings import Settings

logger = get_logger(__name__)


class QueryEngine:
    """Answer questions using the indexed blog content."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ask(self, question: str) -> RAGResponse:
        selected = self._retrieve(question)
        if not selected:
            return RAGResponse(
                answer="I could not find relevant Arnifi blog content for that question.",
                sources=[],
            )
        return self.settings.generator.generate(question, selected)

    def _retrieve(self, question: str) -> list[RetrievedChunk]:
        query_vector = self.settings.embedder.embed_query(question)
        matches = self.settings.vectorstore.query(
            query_vector,
            top_k=self.settings.setting("retrieval", "top_k_initial", default=20),
        )
        return diversify_chunks(
            matches,
            max_chunks_returned=self.settings.setting(
                "retrieval", "max_chunks_returned", default=5
            ),
            max_chunks_per_source_url=self.settings.setting(
                "retrieval", "max_chunks_per_source_url", default=2
            ),
        )

    def ask_stream(self, question: str) -> Iterator[dict[str, Any]]:
        """Same retrieval as ask(), but stream the LLM answer token-by-token."""
        selected = self._retrieve(question)
        if not selected:
            yield {"type": "sources", "sources": []}
            yield {
                "type": "token",
                "content": "I could not find relevant Arnifi blog content for that question.",
            }
            yield {"type": "done"}
            return

        _, sources = self.settings.generator.build_prompt(question, selected)
        yield {"type": "sources", "sources": sources}

        for token in self.settings.generator.generate_stream(question, selected):
            yield {"type": "token", "content": token}
        yield {"type": "done"}

    def inspect(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.settings.state_db.stats()}
        try:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        except Exception as exc:
            result["index_stats_error"] = str(exc)
        return result
