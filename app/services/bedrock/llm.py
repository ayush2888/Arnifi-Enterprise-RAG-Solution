"""
Amazon Nova Lite via Bedrock Converse / ConverseStream.

Generates grounded answers from retrieved chunks. Streaming maps Bedrock
deltas onto the same token iterator the FastAPI SSE endpoint already expects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from app.models.schemas import RAGResponse, RetrievedChunk
from app.services.bedrock.client import create_bedrock_runtime_client
from app.services.prompting.loader import load_prompt
from app.utils.helpers import get_logger

logger = get_logger(__name__)

# this class is used to generate answers from the retrived chunks
class NovaLiteLLM:
    """LLM implementation backed by Amazon Nova Lite on Bedrock."""

    def __init__(
        self,
        model_id: str,
        region: str,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        prompt_path: str | Path | None = None,
        client: Any | None = None,
    ) -> None:
        self.model_id = model_id
        self.region = region
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = load_prompt(prompt_path)
        self._client = client or create_bedrock_runtime_client(region)

    def build_prompt(
        self, question: str, chunks: list[RetrievedChunk]
    ) -> tuple[str, list[dict]]:
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
            "Context:\n" + "\n\n---\n\n".join(context_blocks)
        )
        return user_prompt, sources

# this method is used to generate a single answer from the retrived chunks
# it uses the build_prompt method to build the prompt and then uses the converse method to generate the answer
    def generate(self, question: str, chunks: list[RetrievedChunk]) -> RAGResponse:
        user_prompt, sources = self.build_prompt(question, chunks)
        logger.info("Generating answer with Nova Lite (%d chunks)", len(chunks))
        response = self._client.converse(
            modelId=self.model_id,
            system=[{"text": self.system_prompt}],
            messages=[
                {"role": "user", "content": [{"text": user_prompt}]},
            ],
            inferenceConfig={
                "temperature": self.temperature,
                "maxTokens": self.max_tokens,
            },
        )
        answer = self._extract_text(response)
        return RAGResponse(answer=answer.strip(), sources=sources)

    def generate_stream(
        self, question: str, chunks: list[RetrievedChunk]
    ) -> Iterator[str]:
        user_prompt, _ = self.build_prompt(question, chunks)
        logger.info("Streaming answer with Nova Lite (%d chunks)", len(chunks))
        stream = self._client.converse_stream(
            modelId=self.model_id,
            system=[{"text": self.system_prompt}],
            messages=[
                {"role": "user", "content": [{"text": user_prompt}]},
            ],
            inferenceConfig={
                "temperature": self.temperature,
                "maxTokens": self.max_tokens,
            },
        )
        for event in stream.get("stream") or []:
            delta = event.get("contentBlockDelta", {}).get("delta", {})
            text = delta.get("text")
            if text:
                yield text

# this method is used to extract the text from the response
# it uses the output and message to extract the text
    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        message = (response.get("output") or {}).get("message") or {}
        parts: list[str] = []
        for block in message.get("content") or []:
            if "text" in block:
                parts.append(block["text"])
        return "".join(parts)
