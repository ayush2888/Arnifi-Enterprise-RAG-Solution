"""Ingest structured team/leadership bios, interview Q&A, and press quotes.

Source: manually curated / deduplicated paste (not a website scrape).
Records live in data/eval/team_leadership_records.json.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config.settings import Settings
from app.models.schemas import Chunk
from app.utils.helpers import get_logger

logger = get_logger(__name__)

DEFAULT_RECORDS_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "eval" / "team_leadership_records.json"
)

# Synthetic but stable source_url namespace for manually provided leadership content.
MANUAL_SOURCE_BASE = "https://arnifi.com/internal/team-leadership"


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_records(path: Path | None = None) -> dict[str, Any]:
    records_path = path or DEFAULT_RECORDS_PATH
    return json.loads(records_path.read_text(encoding="utf-8"))


def build_chunks_from_records(payload: dict[str, Any]) -> list[Chunk]:
    """One chunk per bio, per Q&A pair, per press_quote record."""
    chunks: list[Chunk] = []
    crawl_ts = _now()

    for bio in payload.get("bios") or []:
        name = str(bio["person_name"])
        role = str(bio.get("role") or "")
        tenure = str(bio.get("tenure") or "")
        background = str(bio.get("background") or "")
        products = bio.get("company_products_led") or []
        products_line = ", ".join(str(p) for p in products) if products else "n/a"
        text = (
            f"{name} is {role} at Arnifi. {tenure}.\n\n"
            f"{background}\n\n"
            f"Products / platforms associated: {products_line}."
        )
        source_url = f"{MANUAL_SOURCE_BASE}/bio/{_stable_id(name)[:12]}"
        chunk_id = _stable_id("team_bio", name, text[:80])
        embed_text = (
            f"ContentType: team_bio\n"
            f"Person: {name}\n"
            f"Role: {role}\n"
            f"{text}"
        )
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                source_url=source_url,
                source_domain="arnifi.com",
                doc_title=f"{name} ΓÇö {role}",
                section_id=_stable_id("bio", name)[:16],
                heading_path=f"Team Bio > {name}",
                heading_text=f"{name} bio",
                chunk_index=0,
                chunk_text=text,
                embed_text=embed_text,
                chunk_char_len=len(text),
                crawl_ts=crawl_ts,
                content_sha1=hashlib.sha1(text.encode("utf-8")).hexdigest(),
                source_type="manually_provided",
                page_kind="team_bio",
                content_type="team_bio",
                person_name=name,
                source_note=str(bio.get("source_note") or "") or None,
            )
        )

    interview = payload.get("interview") or {}
    person = str(interview.get("person_name") or "Manu Midha")
    source_note = str(interview.get("source_note") or interview.get("interview_source") or "")
    interview_url = str(
        interview.get("canonical_url")
        or f"{MANUAL_SOURCE_BASE}/interview/{_stable_id(person)[:12]}"
    )
    for idx, pair in enumerate(interview.get("qa_pairs") or []):
        question = str(pair.get("question") or "").strip()
        answer = str(pair.get("answer") or "").strip()
        if not question or not answer:
            continue
        text = f"Q: {question}\n\nA: {answer}"
        # Help retrieval for philosophy / why-pricing style questions.
        if "fixed pricing" in question.lower() or "fixed-price" in answer.lower():
            text = (
                "Arnifi fixed pricing philosophy (CEO Manu Midha, EnterpriseAM interview).\n\n"
                + text
            )
        chunk_id = _stable_id("team_interview", person, question)
        embed_text = (
            f"ContentType: team_interview\n"
            f"Person: {person}\n"
            f"Interview: {interview.get('interview_source') or 'interview'}\n"
            f"{text}"
        )
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                source_url=interview_url,
                source_domain=urlparse(interview_url).netloc or "enterpriseam.com",
                doc_title=f"{person} ΓÇö {interview.get('interview_source') or 'Interview'}",
                section_id=_stable_id("qa", person, question)[:16],
                heading_path=f"Interview > {person} > Q{idx + 1}",
                heading_text=question,
                chunk_index=idx,
                chunk_text=text,
                embed_text=embed_text,
                chunk_char_len=len(text),
                crawl_ts=crawl_ts,
                content_sha1=hashlib.sha1(text.encode("utf-8")).hexdigest(),
                source_type="manually_provided",
                page_kind="team_interview",
                content_type="team_interview",
                person_name=person,
                source_note=source_note or None,
            )
        )

    for press in payload.get("press_records") or []:
        related = str(press.get("related_person") or "")
        headline = str(press.get("headline") or "")
        body = str(press.get("body") or "")
        quotes = press.get("notable_quotes") or []
        quote_block = "\n\n".join(f'"{q}"' for q in quotes if q)
        text = f"{headline}\n\n{body}"
        if quote_block:
            text = f"{text}\n\nNotable quotes:\n{quote_block}"
        # Prefer synthetic URL keyed by headline so we don't collide with existing
        # press_release source_url (full body already in index).
        source_url = f"{MANUAL_SOURCE_BASE}/press-quote/{_stable_id(headline, related)[:16]}"
        chunk_id = _stable_id("press_quote", headline, related)
        embed_text = (
            f"ContentType: press_quote\n"
            f"RelatedPerson: {related}\n"
            f"Headline: {headline}\n"
            f"{text}"
        )
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                source_url=source_url,
                source_domain="arnifi.com",
                doc_title=headline,
                section_id=_stable_id("press", headline)[:16],
                heading_path=f"Press Quote > {related or 'company'}",
                heading_text=headline,
                chunk_index=0,
                chunk_text=text,
                embed_text=embed_text,
                chunk_char_len=len(text),
                crawl_ts=crawl_ts,
                content_sha1=hashlib.sha1(text.encode("utf-8")).hexdigest(),
                source_type="manually_provided",
                page_kind="press_quote",
                content_type="press_quote",
                related_person=related or None,
                person_name=related or None,
                source_note=str(press.get("source_note") or "") or None,
            )
        )

    return chunks


class TeamLeadershipIndexer:
    """Upsert curated team/leadership chunks to Pinecone."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ingest(
        self,
        *,
        records_path: Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        payload = load_records(records_path)
        chunks = build_chunks_from_records(payload)

        bios = payload.get("bios") or []
        interview = payload.get("interview") or {}
        qa_count = len(interview.get("qa_pairs") or [])
        press = payload.get("press_records") or []

        coverage = {
            "bio_people": [b.get("person_name") for b in bios],
            "bio_count": len(bios),
            "interview_person": interview.get("person_name"),
            "interview_qa_count": qa_count,
            "press_count": len(press),
            "arnios_dedupe": next(
                (
                    p.get("dedupe")
                    for p in press
                    if "Dhanbad" in str(p.get("headline") or "")
                ),
                None,
            ),
            "existing_arnios_press_url": next(
                (
                    p.get("existing_press_url")
                    for p in press
                    if p.get("existing_press_url")
                ),
                None,
            ),
        }

        # Validation gates from Task 5
        required = {"Manu Midha", "Shashi Kumar", "Tulika Saxena"}
        missing = required - set(coverage["bio_people"])
        if missing or qa_count != 7:
            return {
                "aborted": True,
                "reason": "coverage_gate",
                "missing_bios": sorted(missing),
                "interview_qa_count": qa_count,
                "expected_qa": 7,
                "coverage": coverage,
            }

        upserted = 0
        purged = 0
        if chunks and not dry_run:
            seen: set[str] = set()
            for ch in chunks:
                if ch.source_url in seen:
                    continue
                seen.add(ch.source_url)
                try:
                    purged += self.settings.vectorstore.delete_by_filter(
                        {"source_url": {"$eq": ch.source_url}}
                    )
                except Exception as exc:
                    logger.warning("Purge skipped for %s: %s", ch.source_url, exc)
            vectors = self.settings.embedder.embed_texts(
                [c.embed_text for c in chunks]
            )
            upserted = self.settings.vectorstore.upsert_chunks(chunks, vectors)

        result: dict[str, Any] = {
            "aborted": False,
            "dry_run": dry_run,
            "chunks_prepared": len(chunks),
            "chunks_upserted": upserted,
            "source_urls_purged": purged,
            "coverage": coverage,
            "chunk_summary": {
                "team_bio": sum(1 for c in chunks if c.content_type == "team_bio"),
                "team_interview": sum(
                    1 for c in chunks if c.content_type == "team_interview"
                ),
                "press_quote": sum(
                    1 for c in chunks if c.content_type == "press_quote"
                ),
            },
        }
        if not dry_run and chunks:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        return result
