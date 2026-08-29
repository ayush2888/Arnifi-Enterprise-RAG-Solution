"""Build WhatsApp deep links so Open-in-WhatsApp lands on that chat."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, unquote

_CHAT_ID_RE = re.compile(r"^[\w.-]+@(g|c)\.us$", re.IGNORECASE)
_INVITE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")
_PHONE_RE = re.compile(r"^\d{8,15}$")
_SOURCE_CHAT_RE = re.compile(
    r"^whatsapp://chat/([^/]+)/episode/",
    re.IGNORECASE,
)


def chat_id_from_source_url(url: str | None) -> str | None:
    """Parse chat_id from whatsapp://chat/{chat_id}/episode/{episode_id}."""
    if not url or not isinstance(url, str):
        return None
    m = _SOURCE_CHAT_RE.match(url.strip())
    if not m:
        return None
    cid = unquote(m.group(1)).strip()
    if not _CHAT_ID_RE.match(cid):
        return None
    return cid


def invite_code_from_link(invite_link: str | None) -> str | None:
    """Extract the group invite code from https://chat.whatsapp.com/{code}."""
    if not invite_link or not isinstance(invite_link, str):
        return None
    parsed = urlparse(invite_link.strip())
    host = (parsed.netloc or "").lower()
    if host not in {"chat.whatsapp.com", "www.chat.whatsapp.com"}:
        return None
    parts = [p for p in unquote(parsed.path).split("/") if p]
    if not parts:
        return None
    code = parts[0].strip()
    if not _INVITE_CODE_RE.match(code):
        return None
    return code


def dm_phone_from_chat_id(chat_id: str) -> str | None:
    """Digits from a 1:1 WhatsApp id like 9198xxxx@c.us."""
    cid = (chat_id or "").strip()
    if not _CHAT_ID_RE.match(cid):
        return None
    local, _, domain = cid.partition("@")
    if domain.lower() != "c.us":
        return None
    digits = re.sub(r"\D", "", local)
    if not _PHONE_RE.match(digits):
        return None
    return digits


def whatsapp_open_urls(
    *,
    chat_id: str,
    invite_link: str | None,
) -> dict[str, str | None]:
    """Return open_url (native app) and fallback_url (https).

    Groups: whatsapp://chat?code=CODE + https://chat.whatsapp.com/CODE
    DMs: https://wa.me/{digits} for both (no group invite).
    """
    cid = (chat_id or "").strip()
    if not _CHAT_ID_RE.match(cid):
        return {"open_url": None, "fallback_url": None}

    phone = dm_phone_from_chat_id(cid)
    if phone:
        wa_me = f"https://wa.me/{phone}"
        return {"open_url": wa_me, "fallback_url": wa_me}

    code = invite_code_from_link(invite_link)
    if not code:
        return {"open_url": None, "fallback_url": None}

    https_invite = f"https://chat.whatsapp.com/{code}"
    return {
        "open_url": f"whatsapp://chat?code={code}",
        "fallback_url": https_invite,
    }


def enrich_whatsapp_sources(
    sources: list[dict[str, Any]],
    *,
    get_invite_link,
) -> list[dict[str, Any]]:
    """Attach group invite / open URLs from the wa_chats registry to RAG sources.

    Prefers ``group_invite_link`` already on the source (Pinecone metadata),
    then falls back to ``get_invite_link(chat_id)`` from the sync registry.
    """
    enriched: list[dict[str, Any]] = []
    for raw in sources:
        item = dict(raw)
        url = str(item.get("source_url") or "")
        source_type = str(item.get("source_type") or "").lower()
        is_wa = source_type == "whatsapp" or url.startswith("whatsapp://")
        if not is_wa:
            enriched.append(item)
            continue

        cid = chat_id_from_source_url(url)
        item["chat_id"] = cid
        invite: str | None = None
        existing = item.get("group_invite_link")
        if isinstance(existing, str) and existing.strip():
            invite = existing.strip()
        elif cid:
            try:
                invite = get_invite_link(cid)
            except Exception:
                invite = None
        if invite and not isinstance(invite, str):
            invite = None
        open_urls = (
            whatsapp_open_urls(chat_id=cid, invite_link=invite)
            if cid
            else {"open_url": None, "fallback_url": None}
        )
        item["group_invite_link"] = invite
        item["open_url"] = open_urls.get("open_url")
        item["fallback_url"] = open_urls.get("fallback_url")
        enriched.append(item)
    return enriched
