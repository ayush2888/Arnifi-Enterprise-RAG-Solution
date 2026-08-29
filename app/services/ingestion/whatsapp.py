"""
WhatsApp (Periskope) sync + episode summarize + Pinecone ingest.

- Sync: list groups, download full message history → data/artifacts/whatsapp/
- Ingest: normalize → episodes → Nova summary → Titan → Pinecone (source_type=whatsapp)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.models.schemas import Chunk
from app.services.periskope.client import PeriskopeChat, PeriskopeClient
from app.services.prompting.loader import load_prompt
from app.utils.helpers import clean_text, get_logger

if TYPE_CHECKING:
    from app.config.settings import Settings

logger = get_logger(__name__)


@dataclass(frozen=True)
class NormalizedMessage:
    message_id: str
    chat_id: str
    body: str
    timestamp: datetime
    from_me: bool
    sender_phone: str | None


@dataclass(frozen=True)
class Episode:
    episode_id: str
    chat_id: str
    messages: list[NormalizedMessage]
    content_sha1: str
    started_at: datetime
    ended_at: datetime


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-@]+", "_", name.strip())
    return (cleaned or "chat")[:160]


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_messages(raw_messages: list[dict[str, Any]]) -> list[NormalizedMessage]:
    """Keep text-bearing messages; sort oldest → newest."""
    normalized: list[NormalizedMessage] = []
    for raw in raw_messages or []:
        if raw.get("is_deleted"):
            continue
        body = clean_text(str(raw.get("body") or ""))
        if not body:
            continue
        ts = _parse_timestamp(raw.get("timestamp"))
        if ts is None:
            continue
        message_id = str(
            raw.get("unique_id") or raw.get("message_id") or ""
        ).strip()
        if not message_id:
            message_id = hashlib.sha1(
                f"{raw.get('chat_id')}|{ts.isoformat()}|{body}".encode()
            ).hexdigest()[:24]
        chat_id = str(raw.get("chat_id") or "").strip()
        sender = raw.get("sender_phone")
        normalized.append(
            NormalizedMessage(
                message_id=message_id,
                chat_id=chat_id,
                body=body,
                timestamp=ts,
                from_me=bool(raw.get("from_me")),
                sender_phone=str(sender).strip() if sender else None,
            )
        )
    normalized.sort(key=lambda m: (m.timestamp, m.message_id))
    return normalized


def _episode_content_sha1(messages: list[NormalizedMessage]) -> str:
    payload = "\n".join(
        f"{m.timestamp.isoformat()}|{m.from_me}|{m.body}" for m in messages
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _episode_id(chat_id: str, messages: list[NormalizedMessage]) -> str:
    first = messages[0].message_id
    last = messages[-1].message_id
    return hashlib.sha1(f"{chat_id}|{first}|{last}".encode()).hexdigest()


def build_episodes(
    messages: list[NormalizedMessage],
    *,
    gap_minutes: int = 30,
    max_messages: int = 40,
    min_messages: int = 3,
) -> list[Episode]:
    """Split a chat timeline into time-gap / size-capped episodes."""
    if not messages:
        return []

    gap_seconds = max(gap_minutes, 1) * 60
    max_messages = max(max_messages, 1)
    min_messages = max(min_messages, 1)

    episodes: list[Episode] = []
    current: list[NormalizedMessage] = [messages[0]]

    def flush() -> None:
        nonlocal current
        if len(current) >= min_messages:
            chat_id = current[0].chat_id
            episodes.append(
                Episode(
                    episode_id=_episode_id(chat_id, current),
                    chat_id=chat_id,
                    messages=list(current),
                    content_sha1=_episode_content_sha1(current),
                    started_at=current[0].timestamp,
                    ended_at=current[-1].timestamp,
                )
            )
        current = []

    for msg in messages[1:]:
        gap = (msg.timestamp - current[-1].timestamp).total_seconds()
        if gap > gap_seconds or len(current) >= max_messages:
            flush()
            current = [msg]
        else:
            current.append(msg)
    if current:
        flush()
    return episodes


def format_episode_for_prompt(episode: Episode, chat_name: str) -> str:
    lines = [
        f"Chat: {chat_name}",
        f"Chat ID: {episode.chat_id}",
        f"Window: {episode.started_at.isoformat()} to {episode.ended_at.isoformat()}",
        f"Messages ({len(episode.messages)}):",
        "",
    ]
    for msg in episode.messages:
        role = "Agent" if msg.from_me else "Customer"
        lines.append(f"[{msg.timestamp.isoformat()}] {role}: {msg.body}")
    return "\n".join(lines)


def summary_to_chunk(
    *,
    summary: str,
    episode: Episode,
    chat_name: str,
    embedding_model: str = "amazon.titan-embed-text-v2:0",
    invite_link: str | None = None,
) -> Chunk:
    text = clean_text(summary)
    date_range = (
        f"{episode.started_at.date().isoformat()} to {episode.ended_at.date().isoformat()}"
    )
    source_url = f"whatsapp://chat/{episode.chat_id}/episode/{episode.episode_id}"
    embed_text = f"Chat: {chat_name}\nWindow: {date_range}\n{text}"
    chunk_id = hashlib.sha1(
        f"whatsapp|{episode.chat_id}|{episode.episode_id}|{embedding_model}".encode()
    ).hexdigest()
    content_sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()
    crawl_ts = datetime.now(timezone.utc).isoformat()
    invite = (invite_link or "").strip() or None
    return Chunk(
        chunk_id=chunk_id,
        source_url=source_url,
        source_domain="whatsapp",
        doc_title=chat_name,
        doc_published_at=episode.ended_at.isoformat(),
        doc_category="whatsapp_episode",
        section_id=f"wa-{episode.episode_id[:16]}",
        heading_path=f"Episode summary · {date_range}",
        heading_text=date_range,
        chunk_index=0,
        chunk_text=text,
        embed_text=embed_text,
        chunk_char_len=len(text),
        crawl_ts=crawl_ts,
        content_sha1=content_sha1,
        source_type="whatsapp",
        whatsapp_invite_link=invite,
    )


class WhatsAppSync:
    """Download group chats + full message history to local artifacts."""

    def __init__(self, client: PeriskopeClient, artifacts_dir: str | Path, state_db: Any) -> None:
        self.client = client
        self.state_db = state_db
        base = Path(artifacts_dir)
        self.messages_dir = base / "whatsapp" / "messages"
        self.messages_dir.mkdir(parents=True, exist_ok=True)

    def sync(
        self,
        *,
        limit_chats: int | None = None,
        chat_type: str = "group",
        chat_page_size: int = 100,
        message_page_size: int = 100,
        chat_allowlist: list[str] | None = None,
    ) -> dict[str, Any]:
        chats = self.client.list_chats(
            chat_type=chat_type,
            page_size=chat_page_size,
            limit=limit_chats,
        )
        allow = {c.strip() for c in (chat_allowlist or []) if c and str(c).strip()}
        if allow:
            chats = [c for c in chats if c.chat_id in allow or c.chat_name in allow]

        processed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for chat in chats:
            try:
                result = self._sync_one(chat, message_page_size=message_page_size)
                processed.append(result)
            except Exception as exc:
                logger.error("WhatsApp sync failed for %s: %s", chat.chat_id, exc)
                failed.append(
                    {
                        "chat_id": chat.chat_id,
                        "chat_name": chat.chat_name,
                        "error": str(exc),
                    }
                )

        return {
            "chat_type": chat_type,
            "chats_listed": len(chats),
            "limit_chats": limit_chats,
            "processed": len(processed),
            "failed": len(failed),
            "items": processed,
            "errors": failed,
            "messages_dir": str(self.messages_dir),
        }

    def _sync_one(self, chat: PeriskopeChat, *, message_page_size: int) -> dict[str, Any]:
        messages = self.client.get_messages(chat.chat_id, page_size=message_page_size)
        invite = PeriskopeClient.extract_invite_link(chat.raw)
        if not invite and not chat.chat_id.lower().endswith("@c.us"):
            try:
                detail = self.client.get_chat(chat.chat_id)
                invite = PeriskopeClient.extract_invite_link(detail)
            except Exception as exc:
                logger.warning(
                    "Could not fetch invite_link for %s: %s",
                    chat.chat_id,
                    exc,
                )
        payload = {
            "chat_id": chat.chat_id,
            "chat_name": chat.chat_name,
            "chat_type": chat.chat_type,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "count": len(messages),
            "messages": messages,
            "chat_raw": chat.raw,
            "invite_link": invite,
        }
        path = self.messages_dir / f"{_safe_filename(chat.chat_id)}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.state_db.upsert_wa_chat(
            chat.chat_id,
            chat.chat_name,
            chat.chat_type,
            message_count=len(messages),
            artifact_path=str(path),
            invite_link=invite,
        )
        logger.info(
            "Synced WhatsApp chat %s (%d messages) -> %s",
            chat.chat_name,
            len(messages),
            path,
        )
        return {
            "chat_id": chat.chat_id,
            "chat_name": chat.chat_name,
            "message_count": len(messages),
            "artifact_path": str(path),
            "invite_link": invite,
        }


class WhatsAppIndexer:
    """Episode summaries → Titan → Pinecone."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.messages_dir = Path(settings.artifacts.base_dir) / "whatsapp" / "messages"
        prompt_path = settings.config_path.parent / "prompts" / "episode_summary.md"
        self.system_prompt = load_prompt(prompt_path)

    def ingest(
        self,
        *,
        limit_chats: int | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        paths = sorted(self.messages_dir.glob("*.json"))
        if not paths:
            return {
                "messages_dir": str(self.messages_dir),
                "chats_found": 0,
                "message": "No WhatsApp message JSON found. Run whatsapp-sync first.",
            }

        if limit_chats is not None:
            paths = paths[: max(limit_chats, 0)]

        gap = int(self.settings.setting("whatsapp", "episode_gap_minutes", default=30))
        max_msgs = int(self.settings.setting("whatsapp", "max_messages_per_episode", default=40))
        min_msgs = int(self.settings.setting("whatsapp", "min_messages", default=3))
        embedding_model = str(
            self.settings.setting(
                "embedding",
                "model",
                default="amazon.titan-embed-text-v2:0",
            )
        )

        all_chunks: list[Chunk] = []
        chat_stats: list[dict[str, Any]] = []
        skipped_unchanged = 0
        summarized = 0
        failed: list[dict[str, Any]] = []

        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                chat_id = str(payload.get("chat_id") or "")
                chat_name = str(payload.get("chat_name") or chat_id)
                invite_link = PeriskopeClient.extract_invite_link(
                    {"invite_link": payload.get("invite_link")}
                )
                if not invite_link:
                    try:
                        invite_link = self.settings.state_db.get_wa_invite_link(chat_id)
                    except Exception:
                        invite_link = None
                messages = normalize_messages(payload.get("messages") or [])
                episodes = build_episodes(
                    messages,
                    gap_minutes=gap,
                    max_messages=max_msgs,
                    min_messages=min_msgs,
                )

                chat_chunks = 0
                chat_skipped = 0
                chat_summarized = 0
                for episode in episodes:
                    prior = self.settings.state_db.get_wa_episode_sha1(episode.episode_id)
                    if not force and prior == episode.content_sha1:
                        skipped_unchanged += 1
                        chat_skipped += 1
                        continue

                    if dry_run:
                        summary = (
                            f"[dry-run] Episode in {chat_name} with "
                            f"{len(episode.messages)} messages covering process discussion."
                        )
                    else:
                        user_prompt = format_episode_for_prompt(episode, chat_name)
                        summary = self.settings.generator.complete(
                            self.system_prompt,
                            user_prompt,
                            max_tokens=600,
                        )
                        summarized += 1
                        chat_summarized += 1

                    chunk = summary_to_chunk(
                        summary=summary,
                        episode=episode,
                        chat_name=chat_name,
                        embedding_model=embedding_model,
                        invite_link=invite_link,
                    )
                    all_chunks.append(chunk)
                    chat_chunks += 1

                    if not dry_run:
                        self.settings.state_db.upsert_wa_episode(
                            episode.episode_id,
                            chat_id,
                            content_sha1=episode.content_sha1,
                            summary_sha1=chunk.content_sha1,
                            message_count=len(episode.messages),
                            started_at=episode.started_at.isoformat(),
                            ended_at=episode.ended_at.isoformat(),
                        )

                chat_stats.append(
                    {
                        "file": path.name,
                        "chat_id": chat_id,
                        "chat_name": chat_name,
                        "messages": len(messages),
                        "episodes": len(episodes),
                        "chunks": chat_chunks,
                        "summarized": chat_summarized,
                        "skipped_unchanged": chat_skipped,
                    }
                )
            except Exception as exc:
                logger.error("WhatsApp ingest failed for %s: %s", path, exc)
                failed.append({"file": path.name, "error": str(exc)})

        upserted = 0
        if all_chunks and not dry_run:
            vectors = self.settings.embedder.embed_texts(
                [chunk.embed_text for chunk in all_chunks]
            )
            upserted = self.settings.vectorstore.upsert_chunks(all_chunks, vectors)

        result: dict[str, Any] = {
            "messages_dir": str(self.messages_dir),
            "chats_found": len(sorted(self.messages_dir.glob("*.json"))),
            "chats_processed": len(chat_stats),
            "chats_failed": len(failed),
            "episodes_skipped_unchanged": skipped_unchanged,
            "episodes_summarized": summarized,
            "chunks_prepared": len(all_chunks),
            "chunks_upserted": upserted,
            "dry_run": dry_run,
            "force": force,
            "chats": chat_stats,
            "errors": failed,
        }
        if not dry_run and all_chunks:
            try:
                result["index_stats"] = self.settings.vectorstore.describe_stats()
            except Exception as exc:
                result["index_stats_error"] = str(exc)
        return result
