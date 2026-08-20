"""Persist usage_events through Supabase REST using the caller's JWT."""

from __future__ import annotations

import os
from typing import Any

import requests

from app.services.usage.context import UsageRecord
from app.services.usage.identity import ChatUser
from app.services.usage.pricing import cost_usd
from app.utils.helpers import get_logger

logger = get_logger(__name__)


def save_usage(user: ChatUser, authorization: str, records: list[UsageRecord]) -> None:
    if not records:
        return
    url = (
        os.environ.get("SUPABASE_URL")
        or os.environ.get("VITE_SUPABASE_URL")
        or ""
    ).strip().rstrip("/").replace("/rest/v1", "")
    key = (
        os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("VITE_SUPABASE_ANON_KEY")
        or ""
    ).strip()
    token = authorization.strip()
    if not url or not key or not token.lower().startswith("bearer "):
        logger.warning("Skipping usage save: SUPABASE_URL / SUPABASE_ANON_KEY not set")
        return
    rows: list[dict[str, Any]] = []
    for rec in records:
        total = rec.prompt_tokens + rec.completion_tokens
        rows.append(
            {
                "user_id": user.id,
                "email": user.email,
                "model_id": rec.model_id,
                "call_kind": rec.call_kind,
                "prompt_tokens": rec.prompt_tokens,
                "completion_tokens": rec.completion_tokens,
                "total_tokens": total,
                "cost_usd": round(cost_usd(rec.model_id, rec.prompt_tokens, rec.completion_tokens), 8),
            }
        )
    try:
        res = requests.post(
            f"{url}/rest/v1/usage_events",
            headers={
                "Authorization": token,
                "apikey": key,
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=rows,
            timeout=8,
        )
        if res.status_code >= 300:
            logger.warning("usage_events insert failed %s: %s", res.status_code, res.text[:500])
        else:
            logger.info("usage_events insert ok (%d rows)", len(rows))
    except requests.RequestException as exc:
        logger.warning("usage_events insert error: %s", exc)
