"""
Central settings: YAML tunables + env secrets + lazy service wiring.

Heavy clients (Bedrock, Pinecone) are created on first use so crawl-only
commands stay light.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os

import yaml

from app.config.env import EnvConfig, load_env
from app.services.bedrock.embeddings import TitanEmbeddings
from app.services.bedrock.llm import NovaLiteLLM
from app.services.ingestion.pipeline import ArtifactStore, Fetcher, StateDB
from app.services.pinecone.store import PineconeStore


class Settings:
    # Settings is the main class that handles the RAG pipeline
    # It is responsible for loading the configuration, initializing the services and closing the services
    def __init__(self, config_path: str | Path, env: EnvConfig | None = None) -> None:
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.env = env or load_env()

        paths = self.config["paths"]
        if self._running_on_lambda():
            # Lambda filesystem is read-only except /tmp.
            tmp = Path("/tmp/arnifi-rag")
            paths = {
                **paths,
                "state_db": str(tmp / "crawl_state.sqlite3"),
                "artifacts_dir": str(tmp / "artifacts"),
            }
        crawl = self.config["crawl"]
        self.state_db = StateDB(paths["state_db"])
        self.artifacts = ArtifactStore(paths["artifacts_dir"])
        self.fetcher = Fetcher(
            delay_seconds=crawl["politeness"]["delay_seconds"],
            timeout_seconds=crawl["request_timeout_seconds"],
            max_retries=crawl["max_retries"],
        )

        self._embedder: TitanEmbeddings | None = None
        self._vectorstore: PineconeStore | None = None
        self._generator: NovaLiteLLM | None = None

    @staticmethod
    def _running_on_lambda() -> bool:
        return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME") or os.getenv("LAMBDA_TASK_ROOT"))

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
    def embedder(self) -> TitanEmbeddings:
        if self._embedder is None:
            embedding = self.config["embedding"]
            self._embedder = TitanEmbeddings(
                model_id=self.env.bedrock_embed_model,
                region=self.env.bedrock_region,
                dimension=int(embedding.get("dimension", 1024)),
                batch_size=int(embedding.get("batch_size", 16)),
            )
        return self._embedder

    # Pinecone store is the vector store for the RAG pipeline
    # This function is responsible for initializing the Pinecone store and returning it , it is used in the folder app/services/pinecone/store.py
    @property
    def vectorstore(self) -> PineconeStore:
        if self._vectorstore is None:
            pinecone_cfg = self.config["pinecone"]
            index_name = self.env.pinecone_index or pinecone_cfg["index_name"]
            region = self.env.pinecone_environment or pinecone_cfg.get(
                "region", "us-east-1"
            )
            self._vectorstore = PineconeStore(
                index_name=index_name,
                dimension=int(self.config["embedding"]["dimension"]),
                namespace=pinecone_cfg.get("namespace", ""),
                metric=pinecone_cfg.get("metric", "cosine"),
                cloud=pinecone_cfg.get("cloud", "aws"),
                region=region,
                api_key=self.env.pinecone_api_key,
            )
        return self._vectorstore

    @property
    def generator(self) -> NovaLiteLLM:
        if self._generator is None:
            llm = self.config["llm"]
            self._generator = NovaLiteLLM(
                model_id=self.env.bedrock_chat_model,
                region=self.env.bedrock_region,
                temperature=float(llm.get("temperature", 0.2)),
                max_tokens=int(llm.get("max_tokens", 1200)),
                prompt_path=self.config_path.parent / "prompts" / "answer_synthesis.md",
            )
        return self._generator

    def close(self) -> None:
        self.state_db.close()
