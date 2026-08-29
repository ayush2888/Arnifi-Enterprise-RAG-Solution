"""
Amazon Titan Embed Text V2 via Bedrock InvokeModel.

Used at ingest time (chunk vectors) and query time (question vector) so both
sides of RAG share the same embedding space.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from app.services.bedrock.client import create_bedrock_runtime_client
from app.services.usage.context import record_usage
from app.utils.helpers import get_logger

logger = get_logger(__name__)


class TitanEmbeddings:
    """Embedder implementation backed by amazon.titan-embed-text-v2:0."""

    def __init__(
        self,
        model_id: str,
        region: str,
        dimension: int = 1024,
        batch_size: int = 16,
        normalize: bool = True,
        client: Any | None = None,
    ) -> None:
        self.model_id = model_id
        self.region = region
        self.dimension = dimension
        # Titan accepts one inputText per request; keep batches small for rate limits.
        self.batch_size = batch_size
        self.normalize = normalize
        self._client = client or create_bedrock_runtime_client(region)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        text_list = list(texts)
        for start in range(0, len(text_list), self.batch_size):
            batch = text_list[start : start + self.batch_size]
            logger.info(
                "Embedding batch %d-%d (Titan Embed Text V2)",
                start + 1,
                start + len(batch),
            )
            for text in batch:
                vectors.append(self._embed_one(text))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vector = self._embed_one(text)
        return vector

    def _embed_one(self, text: str) -> list[float]:
        body = {
            "inputText": text,
            "dimensions": self.dimension,
            "normalize": self.normalize,
        }
        response = self._client.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        payload = json.loads(response["body"].read())
        embedding = payload.get("embedding")
        if not embedding:
            raise RuntimeError(f"Titan embed response missing embedding: {payload}")
        prompt_tokens = int(payload.get("inputTextTokenCount") or 0)
        if not prompt_tokens:
            prompt_tokens = max(1, (len(text) + 3) // 4)
        record_usage(
            model_id=self.model_id,
            call_kind="embed",
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
        )
        return embedding
