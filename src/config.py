"""
Shared settings and services for both pipelines.

Loads config/settings.yaml and wires up SQLite, HTTP, embeddings, Pinecone, and the LLM.
Heavy clients (embedder, vectorstore, generator) are created on first use.
"""



from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.ingest import ArtifactStore, Fetcher, StateDB
from src.query import AnswerGenerator
from src.vectorstore import EmbeddingService, PineconeStore


class Settings:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = self._load_config()

        paths = self.config["paths"]
        crawl = self.config["crawl"]
        self.state_db = StateDB(paths["state_db"])
        self.artifacts = ArtifactStore(paths["artifacts_dir"])
        self.fetcher = Fetcher(
            delay_seconds=crawl["politeness"]["delay_seconds"],
            timeout_seconds=crawl["request_timeout_seconds"],
            max_retries=crawl["max_retries"],
        )

        self._embedder: EmbeddingService | None = None
        self._vectorstore: PineconeStore | None = None
        self._generator: AnswerGenerator | None = None

    def setting(self, *keys: str, default: Any = None) -> Any:
        """Read nested settings, e.g. setting('chunk', 'max_tokens')."""
        value: Any = self.config
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def _load_config(self) -> dict[str, Any]:
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @property
    def embedder(self) -> EmbeddingService:
        if self._embedder is None:
            embedding = self.config["embedding"]
            self._embedder = EmbeddingService(
                model=embedding["model"],
                batch_size=embedding["batch_size"],
                provider=embedding.get("provider", "sentence-transformers"),
            )
        return self._embedder

    @property
    def vectorstore(self) -> PineconeStore:
        if self._vectorstore is None:
            pinecone_cfg = self.config["pinecone"]
            self._vectorstore = PineconeStore(
                index_name=pinecone_cfg["index_name"],
                dimension=self.config["embedding"]["dimension"],
                namespace=pinecone_cfg.get("namespace", ""),
                metric=pinecone_cfg.get("metric", "cosine"),
                cloud=pinecone_cfg.get("cloud", "aws"),
                region=pinecone_cfg.get("region", "us-east-1"),
            )
        return self._vectorstore

    @property
    def generator(self) -> AnswerGenerator:
        if self._generator is None:
            llm = self.config["llm"]
            self._generator = AnswerGenerator(
                model=llm["model"],
                temperature=llm["temperature"],
                max_tokens=llm["max_tokens"],
                prompt_path=self.config_path.parent / "prompts" / "answer_synthesis.md",
            )
        return self._generator

    def close(self) -> None:
        self.state_db.close()
