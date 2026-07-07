"""
Embedding vectors and Pinecone storage.

Used by both pipelines: ingest embeds chunks before upsert; query embeds the question before search.
"""

from __future__ import annotations

import os
import time
from typing import Any, Sequence

from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec

from src.models import Chunk, RetrievedChunk
from src.utils import get_logger

logger = get_logger(__name__)


class EmbeddingService:
    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        batch_size: int = 64,
        provider: str = "sentence-transformers",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self.provider = provider.lower().replace("_", "-")
        self._st_model = None
        self._openai_client = None

        if self.provider == "sentence-transformers":
            # Import here so crawl/extract commands don't load the model until indexing.
            from sentence_transformers import SentenceTransformer

            logger.info("Loading sentence-transformers model: %s", model)
            self._st_model = SentenceTransformer(model)
        elif self.provider == "openai":
            self._openai_client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        else:
            raise ValueError(
                f"Unsupported embedding provider: {provider}. "
                "Use 'sentence-transformers' or 'openai'."
            )

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.provider == "sentence-transformers":
            return self._embed_sentence_transformers(texts)
        return self._embed_openai(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def _embed_sentence_transformers(self, texts: Sequence[str]) -> list[list[float]]:
        assert self._st_model is not None
        text_list = list(texts)
        vectors: list[list[float]] = []

        # Batch to avoid loading too many texts into memory at once.
        for start in range(0, len(text_list), self.batch_size):
            batch = text_list[start : start + self.batch_size]
            logger.info("Embedding batch %d-%d (sentence-transformers)", start + 1, start + len(batch))
            encoded = self._st_model.encode(
                batch,
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            vectors.extend(encoded.tolist())
        return vectors

    def _embed_openai(self, texts: Sequence[str]) -> list[list[float]]:
        assert self._openai_client is not None
        text_list = list(texts)
        vectors: list[list[float]] = []

        for start in range(0, len(text_list), self.batch_size):
            batch = text_list[start : start + self.batch_size]
            logger.info("Embedding batch %d-%d (openai)", start + 1, start + len(batch))
            response = self._openai_client.embeddings.create(model=self.model, input=batch)
            vectors.extend([item.embedding for item in response.data])
        return vectors


class PineconeStore:
    # initlize the store with the index name, dimension, namespace, metric, cloud, region, and api key
    # the index name is the name of the index in Pinecone
    # the dimension is the dimension of the vectors
    # the namespace is the namespace of the index
    # the metric is the metric of the index
    # the cloud is the cloud of the index
    # the region is the region of the index
    # the api key is the api key of the index
    
    def __init__(
        self,
        index_name: str,
        dimension: int,
        namespace: str = "",
        metric: str = "cosine",
        cloud: str = "aws",
        region: str = "us-east-1",
        api_key: str | None = None,
    ) -> None:
        self.index_name = index_name
        self.dimension = dimension
        self.namespace = namespace
        self.metric = metric
        self.cloud = cloud
        self.region = region
        self.api_key = api_key or os.getenv("PINECONE_API_KEY")

        self.pc = Pinecone(api_key=self.api_key)
        self._ensure_index()
        self.index = self.pc.Index(self.index_name)

    def _ensure_index(self) -> None:
        existing = {idx.name for idx in self.pc.list_indexes()}
        if self.index_name in existing:
            return

        # Auto-create serverless index on first run so setup is one command.
        logger.info("Creating Pinecone index %s (dim=%s)", self.index_name, self.dimension)
        self.pc.create_index(
            name=self.index_name,
            dimension=self.dimension,
            metric=self.metric,
            spec=ServerlessSpec(cloud=self.cloud, region=self.region),
        )
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        while True:
            desc = self.pc.describe_index(self.index_name)
            if desc.status.get("ready"):
                return
            time.sleep(1)

    def upsert_chunks(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]],
        batch_size: int = 100,
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")

        total = 0
        # Pinecone upserts are batched to stay within API payload limits.
        for start in range(0, len(chunks), batch_size):
            batch_chunks = chunks[start : start + batch_size]
            batch_vectors = vectors[start : start + batch_size]
            records = [
                {
                    "id": chunk.chunk_id,
                    "values": vector,
                    "metadata": chunk.metadata(),
                }
                for chunk, vector in zip(batch_chunks, batch_vectors)
            ]
            self.index.upsert(vectors=records, namespace=self.namespace)
            total += len(records)
            logger.info("Upserted %d vectors to Pinecone", len(records))
        return total

    def query(
        self,
        vector: list[float],
        top_k: int = 20,
        include_metadata: bool = True,
    ) -> list[RetrievedChunk]:
        response = self.index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=include_metadata,
            namespace=self.namespace,
        )
        return [self._to_retrieved_chunk(match) for match in response.get("matches") or []]

    def _to_retrieved_chunk(self, match: dict[str, Any]) -> RetrievedChunk:
        metadata = match.get("metadata") or {}
        return RetrievedChunk(
            chunk_id=match["id"],
            score=float(match.get("score") or 0.0),
            source_url=metadata.get("source_url", ""),
            doc_title=metadata.get("doc_title", ""),
            heading_path=metadata.get("heading_path", ""),
            chunk_text=metadata.get("chunk_text", ""),
            metadata=metadata,
        )

    def describe_stats(self) -> dict[str, Any]:
        return self.index.describe_index_stats()
