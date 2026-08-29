"""
Amazon Nova Lite via Bedrock Converse / ConverseStream.

Generates grounded answers from retrieved chunks. Streaming maps Bedrock
deltas onto the same token iterator the FastAPI SSE endpoint already expects.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from app.models.schemas import RAGResponse, RetrievedChunk
from app.services.bedrock.client import create_bedrock_runtime_client
from app.services.prompting.loader import load_prompt
from app.services.usage.context import record_usage
from app.utils.helpers import get_logger

logger = get_logger(__name__)

_RELATED_SYSTEM = """You suggest short follow-up questions for Arnifi's business knowledge assistant \
(company setup, visas, freezones, funds, compliance, pricing).

Return ONLY a JSON array of exactly 3 strings. No markdown fences, no commentary.
Each question must:
- be a natural follow-up to the given Q&A topic
- be under 90 characters
- not repeat the original question
- be answerable from business/knowledge content (not personal opinions)
"""


def parse_related_questions(raw: str, *, limit: int = 3) -> list[str]:
    """Parse a JSON string array (or fenced JSON) into cleaned question strings."""
    text = (raw or "").strip()
    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if not isinstance(item, str):
            continue
        q = " ".join(item.strip().split())
        if len(q) < 8 or len(q) > 120:
            continue
        out.append(q)
        if len(out) >= limit:
            break
    return out


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
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]] | None = None,
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
                    # Episode summary / passage shown in the UI source modal.
                    "snippet": (chunk.chunk_text or "")[:1200],
                    "source_type": (chunk.metadata or {}).get("source_type")
                    or (
                        "whatsapp"
                        if str(chunk.source_url).startswith("whatsapp://")
                        else None
                    ),
                    "group_invite_link": (chunk.metadata or {}).get(
                        "whatsapp_invite_link"
                    )
                    or None,
                }
            )

        history_block = ""
        if history:
            lines: list[str] = []
            for turn in history[-8:]:
                role = (turn.get("role") or "").strip().lower()
                content = (turn.get("content") or "").strip()
                if role not in {"user", "assistant"} or not content:
                    continue
                label = "User" if role == "user" else "Assistant"
                if role == "assistant" and len(content) > 1200:
                    content = content[:1200] + "…"
                lines.append(f"{label}: {content}")
            if lines:
                history_block = (
                    "Previous conversation (for resolving follow-ups only):\n"
                    + "\n".join(lines)
                    + "\n\n"
                )

        user_prompt = (
            "Use the context below to answer the question.\n\n"
            f"{history_block}"
            f"Current question:\n{question}\n\n"
            "Context:\n" + "\n\n---\n\n".join(context_blocks)
        )
        return user_prompt, sources

# this method is used to generate a single answer from the retrived chunks
# it uses the build_prompt method to build the prompt and then uses the converse method to generate the answer
    def generate(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]] | None = None,
    ) -> RAGResponse:
        user_prompt, sources = self.build_prompt(question, chunks, history=history)
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
        usage = response.get("usage") or {}
        record_usage(
            model_id=self.model_id,
            call_kind="answer",
            prompt_tokens=int(usage.get("inputTokens") or 0),
            completion_tokens=int(usage.get("outputTokens") or 0),
        )
        return RAGResponse(answer=answer.strip(), sources=sources)

    def generate_stream(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[str]:
        user_prompt, _ = self.build_prompt(question, chunks, history=history)
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
            meta = event.get("metadata") or {}
            usage = meta.get("usage") or {}
            if usage:
                record_usage(
                    model_id=self.model_id,
                    call_kind="answer",
                    prompt_tokens=int(usage.get("inputTokens") or 0),
                    completion_tokens=int(usage.get("outputTokens") or 0),
                )
            delta = event.get("contentBlockDelta", {}).get("delta", {})
            text = delta.get("text")
            if text:
                yield text

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """One-shot completion for utilities like episode summarization."""
        logger.info("Nova Lite complete() (%d user chars)", len(user))
        response = self._client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[
                {"role": "user", "content": [{"text": user}]},
            ],
            inferenceConfig={
                "temperature": self.temperature if temperature is None else temperature,
                "maxTokens": self.max_tokens if max_tokens is None else max_tokens,
            },
        )
        text = self._extract_text(response).strip()
        usage = response.get("usage") or {}
        record_usage(
            model_id=self.model_id,
            call_kind="related",
            prompt_tokens=int(usage.get("inputTokens") or 0),
            completion_tokens=int(usage.get("outputTokens") or 0),
        )
        return text

    def suggest_related_questions(self, question: str, answer: str) -> list[str]:
        """Return 3 short follow-up questions grounded in this Q&A topic."""
        user = (
            f"Question:\n{question.strip()}\n\n"
            f"Answer (excerpt):\n{(answer or '').strip()[:900]}"
        )
        try:
            raw = self.complete(
                _RELATED_SYSTEM,
                user,
                max_tokens=220,
                temperature=0.4,
            )
            questions = parse_related_questions(raw)
            logger.info("Related questions generated: %d", len(questions))
            return questions
        except Exception as exc:
            logger.warning("Related question generation failed: %s", exc)
            return []

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        message = (response.get("output") or {}).get("message") or {}
        parts: list[str] = []
        for block in message.get("content") or []:
            if "text" in block:
                parts.append(block["text"])
        return "".join(parts)
