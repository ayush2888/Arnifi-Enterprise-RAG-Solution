"""Resolve the signed-in Supabase user from a Bearer access token."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from app.utils.helpers import get_logger

logger = get_logger(__name__)


@dataclass
class ChatUser:
    id: str
    email: str


def _supabase_url() -> str:
    raw = (
        os.environ.get("SUPABASE_URL")
        or os.environ.get("VITE_SUPABASE_URL")
        or ""
    ).strip()
    return raw.rstrip("/").replace("/rest/v1", "")


def _anon_key() -> str:
    return (
        os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("VITE_SUPABASE_ANON_KEY")
        or ""
    ).strip()


def _bearer_token(authorization: str | None) -> str | None:
    raw = (authorization or "").strip()
    if not raw.lower().startswith("bearer "):
        return None
    token = raw[7:].strip()
    return token or None


def resolve_user(authorization: str | None) -> ChatUser | None:
    token = _bearer_token(authorization)
    url = _supabase_url()
    key = _anon_key()
    if not token or not url or not key:
        return None
    try:
        res = requests.get(
            f"{url}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": key,
            },
            timeout=8,
        )
    except requests.RequestException as exc:
        logger.warning("Auth lookup failed: %s", exc)
        return None
    if res.status_code != 200:
        logger.warning("Auth lookup failed status=%s body=%s", res.status_code, res.text[:200])
        return None
    data: dict[str, Any] = res.json() or {}
    uid = str(data.get("id") or "")
    email = str(data.get("email") or "")
    if not uid:
        return None
    return ChatUser(id=uid, email=email)


def fetch_profile_role(authorization: str | None, user_id: str) -> str | None:
    """Read profiles.role for the signed-in user (RLS: self or admin)."""
    token = _bearer_token(authorization)
    url = _supabase_url()
    key = _anon_key()
    uid = (user_id or "").strip()
    if not token or not url or not key or not uid:
        return None
    try:
        res = requests.get(
            f"{url}/rest/v1/profiles",
            params={"id": f"eq.{uid}", "select": "role"},
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": key,
                "Accept": "application/json",
            },
            timeout=8,
        )
    except requests.RequestException as exc:
        logger.warning("Profile role lookup failed: %s", exc)
        return None
    if res.status_code != 200:
        logger.warning(
            "Profile role lookup failed status=%s body=%s",
            res.status_code,
            res.text[:200],
        )
        return None
    rows = res.json() or []
    if not isinstance(rows, list) or not rows:
        return None
    role = str((rows[0] or {}).get("role") or "").strip().lower()
    return role or None
