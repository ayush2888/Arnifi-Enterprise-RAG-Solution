"""Indexer for Press Releases, Events, and Case Studies."""

from __future__ import annotations

import json
from typing import Any

from app.config.settings import Settings
from app.models.schemas import Chunk
from app.services.ingestion.announcement_content import (
    CASE_STUDIES_LISTING_URL,
    EVENTS_LISTING_URL,
    PRESS_LISTING_URL,
    discover_case_studies,
    discover_events,
    discover_press_releases,
    extract_case_study_from_api,
    extract_event,
    extract_press_release,
    _fetch_html,
    _slug_from_detail_url,
)
from app.services.ingestion.pipeline import chunk_document, sha1_text, should_skip_post
from app.services.prodapi.client import ProdapiClient
from app.utils.helpers import canonicalize_url, get_logger

logger = get_logger(__name__)


class AnnouncementContentIndexer:
    """Discover ΓåÆ extract ΓåÆ chunk ΓåÆ Pinecone for announcement/case content."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: ProdapiClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or ProdapiClient()

    def ingest(
        self,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        press_only: bool = False,
        events_only: bool = False,
        case_studies_only: bool = False,
        skip_unchanged: bool = True,
        allow_partial: bool = False,
    ) -> dict[str, Any]:
        exclusive = sum(
            bool(x) for x in (press_only, events_only, case_studies_only)
        )
        if exclusive > 1:
            return {
                "aborted": True,
                "reason": "conflicting_flags",
                "error": "Pass at most one of --press-only / --events-only / --case-studies-only",
            }

        catalogs: list[dict[str, Any]] = []
        if press_only:
            catalogs.append(discover_press_releases())
        elif events_only:
            catalogs.append(discover_events(archived_only=True))
        elif case_studies_only:
            catalogs.append(discover_case_studies(self.client))
        else:
            catalogs.append(discover_press_releases())
            catalogs.append(discover_events(archived_only=True))
            catalogs.append(discover_case_studies(self.client))

        incomplete = [c for c in catalogs if not c.get("complete")]
        if incomplete and not allow_partial:
            return {
                "aborted": True,
                "reason": "completeness_gate",
                "catalogs": [
                    {
                        "content_type": c.get("content_type"),
                        "listing_url": c.get("listing_url"),
                        "discovery": c.get("discovery"),
                        "confirmed_total": c.get("confirmed_total"),
                        "complete": c.get("complete"),
                        "ambiguity": c.get("ambiguity"),
                        "pagination": c.get("pagination"),
                        "detail_urls": (c.get("detail_urls") or [])[:20],
                    }
                    for c in catalogs
                ],
            }

        jobs: list[dict[str, Any]] = []
        for cat in catalogs:
            ctype = str(cat.get("content_type") or "")
            listing = str(cat.get("listing_url") or "")
            for url in cat.get("detail_urls") or []:
                jobs.append(
                    {
                        "content_type": ctype,
                        "detail_url": url,
                        "listing_url": listing,
                    }
                )
        if limit is not None:
            jobs = jobs[:limit]

        all_chunks: list[Chunk] = []
        new_created = 0
        existing_matched_updated = 0
        failed = 0
        skipped = 0
        failed_detail_urls: list[dict[str, str]] = []
        rows: list[dict[str, Any]] = []
        case_modes: dict[str, str] = {}

        for job in jobs:
            ctype = str(job.get("content_type") or "")
            detail_url = canonicalize_url(str(job.get("detail_url") or ""))
            listing_url = str(job.get("listing_url") or "")
            row: dict[str, Any] = {
                "content_type": ctype,
                "detail_url": detail_url,
            }
            try:
                status, chunks, extra = self._index_one(
                    content_type=ctype,
                    detail_url=detail_url,
                    listing_url=listing_url,
                    skip_unchanged=skip_unchanged,
                    dry_run=True,
                )
                row.update(extra)
                row["status"] = status
                if extra.get("chunk_mode"):
                    case_modes[detail_url] = str(extra["chunk_mode"])
                if status == "processed" and chunks:
                    all_chunks.extend(chunks)
                    if extra.get("was_existing"):
                        existing_matched_updated += 1
                    else:
                        new_created += 1
                elif status == "skipped":
                    skipped += 1
                    existing_matched_updated += 1
                    row["matched_existing"] = True
                else:
                    failed += 1
                    failed_detail_urls.append(
                        {
                            "detail_url": detail_url,
                            "error": str(extra.get("error") or status),
                        }
                    )
            except Exception as exc:
                logger.error("announcement ingest failed %s: %s", detail_url, exc)
                row["status"] = "failed"
                row["error"] = str(exc)
                failed += 1
                failed_detail_urls.append(
                    {"detail_url": detail_url, "error": str(exc)}
                )
            rows.append(row)

        if failed and not allow_partial:
            return {
                "aborted": True,
                "reason": "detail_failures",
                "new_created": new_created,
                "existing_matched_updated": existing_matched_updated,
                "pages_skipped": skipped,
                "pages_failed": failed,
                "failed_detail_urls": failed_detail_urls,
                "catalogs": [
                    {
                        "content_type": c.get("content_type"),
                        "confirmed_total": c.get("confirmed_total"),
                        "complete": c.get("complete"),
                        "ambiguity": c.get("ambiguity"),
                    }
                    for c in catalogs
                ],
                "items": rows[:200],
            }

        upserted = 0
        deleted = 0
        if all_chunks and not dry_run:
            seen_urls: set[str] = set()
            for ch in all_chunks:
                if ch.source_url in seen_urls:
                    continue
                seen_urls.add(ch.source_url)
                try:
                    deleted += self.settings.vectorstore.delete_by_filter(
                        {"source_url": {"$eq": ch.source_url}}
                    )
                except Exception as exc:
                    logger.warning("Purge skipped for %s: %s", ch.source_url, exc)
                sha = next(
                    (
                        r.get("doc_sha1")
                        for r in rows
                        if r.get("source_url") == ch.source_url and r.get("doc_sha1")
                    ),
                    None,
                )
                if sha:
                    self.settings.state_db.mark_website_crawled(ch.source_url, str(sha))
            vectors = self.settings.embedder.embed_texts(
                [c.embed_text for c in all_chunks]
            )
            upserted = self.settings.vectorstore.upsert_chunks(all_chunks, vectors)

        result: dict[str, Any] = {
            "aborted": False,
            "discovery": "mixed",
            "jobs": len(jobs),
            "new_created": new_created,
            "existing_matched_updated": existing_matched_updated,
            "pages_skipped": skipped,
            "pages_failed": failed,
            "failed_detail_urls": failed_detail_urls,
            "chunks_prepared": len(all_chunks),
            "chunks_upserted": upserted,
            "source_urls_purged": deleted,
            "dry_run": dry_run,
            "case_study_chunk_modes": case_modes,
            "catalogs": [
                {
                    "content_type": c.get("content_type"),
                    "listing_url": c.get("listing_url"),
                    "discovery": c.get("discovery"),
                    "confirmed_total": c.get("confirmed_total"),
                    "complete": c.get("complete"),
                    "ambiguity": c.get("ambiguity"),
                    "pagination": c.get("pagination"),
                    "notify_me_skipped": c.get("notify_me_skipped"),
                    "main_listing_total": c.get("main_listing_total"),
                    "industry_union_total": c.get("industry_union_total"),
                    "dedupe_dropped": c.get("dedupe_dropped"),
                }
                for c in catalogs
            ],
            "items": rows[:200],
        }
        if not dry_run and all_chunks:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        return result

    def _index_one(
        self,
        *,
        content_type: str,
        detail_url: str,
        listing_url: str,
        skip_unchanged: bool,
        dry_run: bool,
    ) -> tuple[str, list[Chunk] | None, dict[str, Any]]:
        page_kind = content_type
        extra: dict[str, Any] = {"source_url": detail_url}

        if content_type == "press_release":
            html = _fetch_html(detail_url)
            document, meta = extract_press_release(html, detail_url)
            listing = listing_url or PRESS_LISTING_URL
            chunk_kwargs = {
                "content_type": "press_release",
                "publish_date": meta.get("publish_date"),
                "source_publication": meta.get("source_publication"),
            }
        elif content_type == "event":
            html = _fetch_html(detail_url)
            document, meta = extract_event(html, detail_url)
            listing = listing_url or EVENTS_LISTING_URL
            partners = meta.get("partners") or []
            chunk_kwargs = {
                "content_type": "event",
                "event_date": meta.get("event_date"),
                "event_time": meta.get("event_time"),
                "location": meta.get("location"),
                "partners": json.dumps(partners) if partners else None,
            }
        elif content_type == "case_study":
            slug = _slug_from_detail_url(detail_url)
            payload = self.client.get_case_study(slug)
            document, meta = extract_case_study_from_api(payload, detail_url)
            listing = listing_url or CASE_STUDIES_LISTING_URL
            extra["chunk_mode"] = meta.get("chunk_mode")
            chunk_kwargs = {
                "content_type": "case_study",
                "publish_date": meta.get("publish_date"),
                "jurisdiction": meta.get("jurisdiction"),
                "industry": meta.get("industry"),
                "read_time": meta.get("read_time"),
            }
        else:
            raise RuntimeError(f"unknown content_type={content_type!r}")

        extra.update(
            {
                k: meta.get(k)
                for k in (
                    "title",
                    "publish_date",
                    "source_publication",
                    "event_date",
                    "location",
                    "jurisdiction",
                    "industry",
                    "read_time",
                    "chunk_mode",
                )
                if meta.get(k) is not None
            }
        )

        doc_sha1 = sha1_text(document.model_dump_json())
        extra["sections"] = len(document.sections)
        extra["doc_sha1"] = doc_sha1
        prior_sha1 = self.settings.state_db.get_website_content_sha1(detail_url)
        was_existing = bool(prior_sha1)
        extra["was_existing"] = was_existing
        self.settings.state_db.upsert_website_url(detail_url, page_kind=page_kind)
        if skip_unchanged and should_skip_post(prior_sha1, doc_sha1):
            logger.info("Skipping unchanged announcement %s", detail_url)
            return "skipped", None, extra

        self.settings.artifacts.save_extracted(
            detail_url, document.model_dump(), kind="website"
        )
        chunks = chunk_document(
            document,
            max_tokens=self.settings.setting("chunk", "max_tokens", default=400),
            overlap_tokens=self.settings.setting(
                "chunk", "overlap_tokens", default=80
            ),
            embedding_model=self.settings.setting(
                "embedding", "model", default="amazon.titan-embed-text-v2:0"
            ),
            source_type="website",
            page_kind=page_kind,
            listing_url=listing,
            **chunk_kwargs,
        )
        extra["chunks"] = len(chunks)
        if not dry_run:
            try:
                self.settings.vectorstore.delete_by_filter(
                    {"source_url": {"$eq": detail_url}}
                )
            except Exception as exc:
                logger.warning("Purge skipped for %s: %s", detail_url, exc)
            self.settings.state_db.mark_website_crawled(detail_url, doc_sha1)
        return "processed", chunks, extra
