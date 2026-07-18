"""
Provider-agnostic interfaces for embeddings, LLM, and vector storage.

Business logic (retrieval, ingestion) depends on these Protocols so Bedrock,
Pinecone, or future providers can be swapped without rewriting RAG flow.
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol, Sequence

from app.models.schemas import Chunk, RAGResponse, RetrievedChunk


class Embedder(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed one or more texts for indexing."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string for retrieval."""
        ...


class LLM(Protocol):
    def generate(self, question: str, chunks: list[RetrievedChunk]) -> RAGResponse:
        """Produce a full grounded answer."""
        ...

    def generate_stream(self, question: str, chunks: list[RetrievedChunk]) -> Iterator[str]:
        """Yield answer tokens for SSE streaming."""
        ...

    def build_prompt(
        self, question: str, chunks: list[RetrievedChunk]
    ) -> tuple[str, list[dict]]:
        """Return (user_prompt, sources) used by streaming and non-streaming paths."""
        ...


class VectorStore(Protocol):
    def upsert_chunks(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]],
        batch_size: int = 100,
    ) -> int:
        ...

    def query(
        self,
        vector: list[float],
        top_k: int = 20,
        include_metadata: bool = True,
    ) -> list[RetrievedChunk]:
        ...

    def describe_stats(self) -> dict[str, Any]:
        ...
