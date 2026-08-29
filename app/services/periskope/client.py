"""
Periskope WhatsApp API client.

Auth: Authorization Bearer + x-phone header.
Endpoints: GET /v1/chats, GET /v1/chats/{chat_id}/messages
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.utils.helpers import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.periskope.app/v1"


@dataclass(frozen=True)
class PeriskopeChat:
    chat_id: str
    chat_name: str
    chat_type: str
    raw: dict[str, Any]


class PeriskopeClient:
    def __init__(
        self,
        api_key: str,
        phone: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = 60,
        delay_seconds: float = 0.35,
    ) -> None:
        if not api_key.strip():
            raise ValueError("PERISKOPE_API_KEY is empty")
        if not phone.strip():
            raise ValueError("PERISKOPE_PHONE is empty")

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
                "x-phone": phone.strip(),
            }
        )

    def _polite_wait(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._polite_wait()
        url = f"{self.base_url}{path}"
        logger.info("Periskope GET %s params=%s", path, params)
        response = self.session.get(url, params=params or {}, timeout=self.timeout_seconds)
        self._last_request_at = time.time()
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Periskope response type: {type(data)}")
        return data

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _post(self, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        self._polite_wait()
        url = f"{self.base_url}{path}"
        logger.info("Periskope POST %s", path)
        response = self.session.post(
            url,
            json=json_body if json_body is not None else {},
            timeout=self.timeout_seconds,
        )
        self._last_request_at = time.time()
        if not response.ok:
            detail = (response.text or "").strip()[:400]
            raise RuntimeError(
                f"Periskope POST {path} failed ({response.status_code}): {detail or response.reason}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Periskope response type: {type(data)}")
        return data

    def list_chats(
        self,
        *,
        chat_type: str = "group",
        page_size: int = 100,
        limit: int | None = None,
    ) -> list[PeriskopeChat]:
        """Paginate GET /chats until exhausted or limit reached.

        Note: Periskope's ``count`` field often mirrors the page size, not the
        org-wide total — so pagination stops on a short/empty page (or when a
        page adds no new chat_ids), not on ``count``.
        """
        chats: list[PeriskopeChat] = []
        seen_ids: set[str] = set()
        offset = 0
        while True:
            if limit is not None and len(chats) >= limit:
                break
            page_limit = page_size
            if limit is not None:
                page_limit = min(page_size, max(limit - len(chats), 1))

            data = self._get(
                "/chats",
                params={
                    "chat_type": chat_type,
                    "limit": page_limit,
                    "offset": offset,
                },
            )
            batch = data.get("chats") or []
            if not batch:
                break

            new_on_page = 0
            for raw in batch:
                chat_id = str(raw.get("chat_id") or "").strip()
                if not chat_id or chat_id in seen_ids:
                    continue
                seen_ids.add(chat_id)
                chats.append(
                    PeriskopeChat(
                        chat_id=chat_id,
                        chat_name=str(raw.get("chat_name") or chat_id).strip(),
                        chat_type=str(raw.get("chat_type") or chat_type).strip(),
                        raw=raw,
                    )
                )
                new_on_page += 1
                if limit is not None and len(chats) >= limit:
                    break

            offset += len(batch)
            if new_on_page == 0 or len(batch) < page_limit:
                break
            if limit is not None and len(chats) >= limit:
                break

        return chats

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        """GET /chats/{chat_id} — includes invite_link when Periskope has one."""
        encoded = quote(chat_id, safe="@.")
        return self._get(f"/chats/{encoded}")

    def refresh_invite(self, chat_id: str) -> str | None:
        """POST /chats/{chat_id}/invite — create/refresh WhatsApp invite link."""
        encoded = quote(chat_id, safe="@.")
        data = self._post(f"/chats/{encoded}/invite")
        return self.extract_invite_link(data)

    @staticmethod
    def extract_invite_link(chat: dict[str, Any]) -> str | None:
        raw = chat.get("invite_link")
        if not isinstance(raw, str):
            return None
        link = raw.strip()
        if not link.lower().startswith("https://chat.whatsapp.com/"):
            return None
        return link

    def get_messages(
        self,
        chat_id: str,
        *,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Paginate GET /chats/{id}/messages for full history."""
        encoded = quote(chat_id, safe="@.")
        messages: list[dict[str, Any]] = []
        offset = 0
        seen_ids: set[str] = set()

        while True:
            data = self._get(
                f"/chats/{encoded}/messages",
                params={"limit": page_size, "offset": offset},
            )
            batch = data.get("messages") or []
            if not batch:
                break

            new_in_page = 0
            for msg in batch:
                mid = str(
                    msg.get("unique_id")
                    or msg.get("message_id")
                    or ""
                ).strip()
                if mid and mid in seen_ids:
                    continue
                if mid:
                    seen_ids.add(mid)
                messages.append(msg)
                new_in_page += 1

            offset += len(batch)
            total = data.get("count")
            if isinstance(total, int) and offset >= total:
                break
            if len(batch) < page_size:
                break
            if new_in_page == 0:
                break

        return messages
