"""
Shared Amazon Bedrock Runtime client.

Bedrock hosts foundation models (Nova, Titan) so we do not run LLMs or
embedding models on Lambda ourselves.
"""

from __future__ import annotations

import boto3
from botocore.config import Config


def create_bedrock_runtime_client(region: str):
    """Create a bedrock-runtime client for the given region."""
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            read_timeout=120,
            connect_timeout=10,
        ),
    )
