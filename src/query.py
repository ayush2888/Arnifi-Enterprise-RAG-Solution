"""
Online query pipeline — read top to bottom to follow the data path.

  1. Embed the user question
  2. Search Pinecone for similar chunks
  3. Pick diverse chunks (not all from one blog post)
  4. Ask the LLM to answer using those chunks
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from openai import OpenAI

from src.models import RAGResponse, RetrievedChunk
from src.utils import get_logger

if TYPE_CHECKING:
    from src.config import Settings

logger = get_logger(__name__)


# --- Step 3: diversify retrieval results ---


def diversify_chunks(
    matches: list[RetrievedChunk],
    *,
    max_chunks_returned: int = 5,
    max_chunks_per_source_url: int = 2,
) -> list[RetrievedChunk]:
    # Without this cap, all top hits might come from one long blog post.
    selected: list[RetrievedChunk] = []
    per_source: dict[str, int] = {}

    for match in sorted(matches, key=lambda m: m.score, reverse=True):
        used_from_url = per_source.get(match.source_url, 0)
        if used_from_url >= max_chunks_per_source_url:
            continue

        selected.append(match)
        per_source[match.source_url] = used_from_url + 1
        if len(selected) >= max_chunks_returned:
            break

    return selected


# --- Step 4: LLM answer generation ---


class AnswerGenerator:
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 1200,
        prompt_path: str | Path | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Groq exposes an OpenAI-compatible API; base_url points there instead of api.openai.com.
        self.client = OpenAI(
            api_key=api_key or os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
        self.system_prompt = self._load_prompt(prompt_path)

    def _load_prompt(self, prompt_path: str | Path | None) -> str:
        if prompt_path is None:
            return (
                "Answer using only the provided context. Cite sources with [Source N]."
            )
        path = Path(prompt_path)
        return path.read_text(encoding="utf-8")

    def _build_prompt(self, question: str, chunks: list[RetrievedChunk]) -> tuple[str, list[dict]]:
        context_blocks: list[str] = []
        sources: list[dict] = []
        for idx, chunk in enumerate(chunks, start=1):
            context_blocks.append(
                f"[Source {idx}]\n"
                f"Title: {chunk.doc_title}\n"
                f"URL: {chunk.source_url}\n"
                f"Section: {chunk.heading_path}\n"
                f"Content:\n{chunk.chunk_text}"
            )
            sources.append(
                {
                    "source_index": idx,
                    "source_url": chunk.source_url,
                    "doc_title": chunk.doc_title,
                    "heading_path": chunk.heading_path,
                    "score": chunk.score,
                }
            )

        user_prompt = (
            "Use the context below to answer the question.\n\n"
            f"Question:\n{question}\n\n"
            "Context:\n"
            + "\n\n---\n\n".join(context_blocks)
        )
        return user_prompt, sources

    def generate(self, question: str, chunks: list[RetrievedChunk]) -> RAGResponse:
        user_prompt, sources = self._build_prompt(question, chunks)

        logger.info("Generating answer with %d context chunks", len(chunks))
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        answer = response.choices[0].message.content or ""
        return RAGResponse(answer=answer.strip(), sources=sources)

    def generate_stream(self, question: str, chunks: list[RetrievedChunk]):
        user_prompt, sources = self._build_prompt(question, chunks)

        logger.info("Streaming answer with %d context chunks", len(chunks))
        stream = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        for event in stream:
            delta = event.choices[0].delta.content
            if delta:
                yield delta


# --- Orchestration: steps 1–4 ---


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
            max_chunks_returned=self.settings.setting("retrieval", "max_chunks_returned", default=5),
            max_chunks_per_source_url=self.settings.setting(
                "retrieval", "max_chunks_per_source_url", default=2
            ),
        )

    def ask_stream(self, question: str) -> Iterator[dict[str, Any]]:
        """Same retrieval as ask(), but stream the LLM answer token-by-token."""
        selected = self._retrieve(question)
        if not selected:
            yield {
                "type": "sources",
                "sources": [],
            }
            yield {
                "type": "token",
                "content": "I could not find relevant Arnifi blog content for that question.",
            }
            yield {"type": "done"}
            return

        _, sources = self.settings.generator._build_prompt(question, selected)
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
