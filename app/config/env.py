"""
Load environment variables used by AWS and Pinecone.

Credentials are never hardcoded — local uses access keys; Lambda uses the
execution role (boto3 picks up the role automatically when keys are absent).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvConfig:
    aws_region: str
    bedrock_region: str
    bedrock_chat_model: str
    bedrock_embed_model: str
    pinecone_api_key: str | None
    pinecone_index: str | None
    pinecone_environment: str


def load_env() -> EnvConfig:
    aws_region = os.getenv("AWS_REGION", "us-east-1")
    # Bedrock models may live in a different region than Lambda (e.g. Mumbai + us-east-1).
    bedrock_region = os.getenv("BEDROCK_REGION") or aws_region
    return EnvConfig(
        aws_region=aws_region,
        bedrock_region=bedrock_region,
        bedrock_chat_model=os.getenv("BEDROCK_CHAT_MODEL", "amazon.nova-lite-v1:0"),
        bedrock_embed_model=os.getenv(
            "BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0"
        ),
        pinecone_api_key=os.getenv("PINECONE_API_KEY"),
        pinecone_index=os.getenv("PINECONE_INDEX"),
        pinecone_environment=os.getenv("PINECONE_ENVIRONMENT", "us-east-1"),
    )
