"""Unit tests for WhatsApp normalize / episodes / chunk mapping."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.ingestion.whatsapp import (
    NormalizedMessage,
    build_episodes,
    normalize_messages,
    summary_to_chunk,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_chat.json"


def _msg(i: int, minutes: int, body: str = "hello") -> NormalizedMessage:
    base = datetime(2026, 6, 12, 5, 0, tzinfo=timezone.utc)
    return NormalizedMessage(
        message_id=f"m{i}",
        chat_id="chat@g.us",
        body=body,
        timestamp=base + timedelta(minutes=minutes),
        from_me=False,
        sender_phone=None,
    )


def test_normalize_messages_from_sample_chat() -> None:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    messages = normalize_messages(payload["messages"])
    assert len(messages) >= 10
    assert messages[0].timestamp <= messages[-1].timestamp
    assert all(m.body for m in messages)


def test_build_episodes_splits_on_gap() -> None:
    messages = [
        _msg(1, 0),
        _msg(2, 5),
        _msg(3, 10),
        _msg(4, 80),  # > 30 min gap
        _msg(5, 85),
        _msg(6, 90),
    ]
    episodes = build_episodes(
        messages,
        gap_minutes=30,
        max_messages=40,
        min_messages=3,
    )
    assert len(episodes) == 2
    assert len(episodes[0].messages) == 3
    assert len(episodes[1].messages) == 3


def test_build_episodes_caps_and_drops_short() -> None:
    messages = [_msg(i, i) for i in range(5)]
    episodes = build_episodes(
        messages,
        gap_minutes=30,
        max_messages=2,
        min_messages=2,
    )
    assert len(episodes) == 2
    assert all(len(ep.messages) == 2 for ep in episodes)

    short = [_msg(1, 0), _msg(2, 1)]
    assert build_episodes(short, min_messages=3) == []


def test_summary_to_chunk_metadata() -> None:
    messages = [_msg(1, 0, "Need KYC list"), _msg(2, 2, "Passport and proof"), _msg(3, 4, "Thanks")]
    episode = build_episodes(messages, min_messages=3)[0]
    chunk = summary_to_chunk(
        summary="Customer asked for KYC documents; agent listed passport and proof of address.",
        episode=episode,
        chat_name="Cayman KYC Group",
    )
    assert chunk.source_type == "whatsapp"
    assert chunk.source_domain == "whatsapp"
    assert chunk.doc_title == "Cayman KYC Group"
    assert chunk.source_url.startswith("whatsapp://chat/")
    assert "KYC" in chunk.chunk_text
    assert chunk.metadata()["source_type"] == "whatsapp"
