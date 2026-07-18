"""Backward-compatible shims — prefer `app.services.pinecone` / `app.services.bedrock`."""

from app.services.bedrock.embeddings import TitanEmbeddings as EmbeddingService
from app.services.pinecone.store import PineconeStore

__all__ = ["EmbeddingService", "PineconeStore"]
