"""
Pinecone vector index client.

Pinecone stores and searches dense vectors produced by Titan; it does not
host the embedding model (bring-your-own-vectors index).
"""

from __future__ import annotations

import os
import time
from typing import Any

from pinecone import Pinecone, ServerlessSpec

from app.models.schemas import Chunk, RetrievedChunk
from app.utils.helpers import get_logger

logger = get_logger(__name__)

# This class is responsible for initializing the Pinecone store and returning it , it is used in the folder app/config/settings.py
class PineconeStore:
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

    # _ensure_index is a function that ensures the Pinecone index exists
    def _ensure_index(self) -> None:
        existing = {idx.name for idx in self.pc.list_indexes()}
        if self.index_name in existing:
            return

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

    # Upsert chunks is a function that upserts a list of chunks and vectors into the Pinecone index
    def upsert_chunks(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]],
        batch_size: int = 100,
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")

        total = 0
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

# Query is a function that queries the Pinecone index for the most similar chunks to a given vector
    def query(
        self,
        vector: list[float],
        top_k: int = 20,
        include_metadata: bool = True,
        filter: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        kwargs: dict[str, Any] = {
            "vector": vector,
            "top_k": top_k,
            "include_metadata": include_metadata,
            "namespace": self.namespace,
        }
        if filter:
            kwargs["filter"] = filter
        response = self.index.query(**kwargs)
        return [self._to_retrieved_chunk(match) for match in response.get("matches") or []]

# _to_retrieved_chunk is a function that converts a Pinecone match to a RetrievedChunk object
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

    def delete_by_filter(self, filter: dict[str, Any]) -> int:
        """
        Delete vectors matching a metadata filter.

        Pinecone does not return a deleted count for filter deletes; we return 1
        on success so callers can treat it as "delete requested".
        """
        if not filter:
            raise ValueError("delete_by_filter requires a non-empty filter")
        logger.info("Deleting Pinecone vectors with filter=%s", filter)
        self.index.delete(filter=filter, namespace=self.namespace)
        return 1

    def delete_ids(self, ids: list[str], batch_size: int = 1000) -> int:
        if not ids:
            return 0
        total = 0
        for start in range(0, len(ids), batch_size):
            batch = ids[start : start + batch_size]
            self.index.delete(ids=batch, namespace=self.namespace)
            total += len(batch)
            logger.info("Deleted %d Pinecone ids", len(batch))
        return total
