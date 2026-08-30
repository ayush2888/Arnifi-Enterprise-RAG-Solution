"""Ingest Arnifi location pages from public prodapi into Pinecone."""

from __future__ import annotations

from typing import Any

from app.config.settings import Settings
from app.models.schemas import Chunk
from app.services.ingestion.pipeline import chunk_document, sha1_text, should_skip_post
from app.services.ingestion.website_sections import extract_country_overview_from_api
from app.services.prodapi.client import ProdapiClient, country_page_url
from app.utils.helpers import canonicalize_url, get_logger

logger = get_logger(__name__)


class ProdapiLocationsIndexer:
    """Fetch /get-countries + /country-overview/:slug ΓåÆ chunk ΓåÆ Pinecone."""

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
        slug: str | None = None,
        skip_unchanged: bool = True,
    ) -> dict[str, Any]:
        countries = self.client.list_countries()
        if slug:
            needle = slug.strip().lower()
            countries = [
                c
                for c in countries
                if str(c.get("slug") or "").lower() == needle
                or str(c.get("countryName") or "").lower() == needle
                or str(c.get("shortcode") or "").lower() == needle
            ]
            if not countries:
                raise RuntimeError(f"No country matched --slug={slug!r}")
        if limit is not None:
            countries = countries[:limit]

        all_chunks: list[Chunk] = []
        processed = skipped = failed = 0
        deleted = 0
        per_country: list[dict[str, Any]] = []

        for country in countries:
            api_slug = str(country.get("slug") or "").strip()
            name = str(country.get("countryName") or api_slug)
            shortcode = str(country.get("shortcode") or "").strip()
            source_url = canonicalize_url(country_page_url(shortcode, slug=api_slug))
            row: dict[str, Any] = {
                "name": name,
                "slug": api_slug,
                "shortcode": shortcode,
                "source_url": source_url,
            }
            try:
                status, chunks, extra = self._index_one(
                    api_slug=api_slug,
                    source_url=source_url,
                    skip_unchanged=skip_unchanged,
                    dry_run=dry_run,
                )
                row.update(extra)
                row["status"] = status
                if status == "processed" and chunks:
                    all_chunks.extend(chunks)
                    processed += 1
                    deleted += int(extra.get("deleted") or 0)
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.error("prodapi location ingest failed %s: %s", api_slug, exc)
                row["status"] = "failed"
                row["error"] = str(exc)
                failed += 1
            per_country.append(row)

        upserted = 0
        if all_chunks and not dry_run:
            vectors = self.settings.embedder.embed_texts([c.embed_text for c in all_chunks])
            upserted = self.settings.vectorstore.upsert_chunks(all_chunks, vectors)

        result: dict[str, Any] = {
            "countries_seen": len(countries),
            "pages_processed": processed,
            "pages_skipped": skipped,
            "pages_failed": failed,
            "chunks_prepared": len(all_chunks),
            "chunks_upserted": upserted,
            "source_urls_purged": deleted,
            "dry_run": dry_run,
            "countries": per_country,
        }
        if not dry_run and all_chunks:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        return result

    def _index_one(
        self,
        *,
        api_slug: str,
        source_url: str,
        skip_unchanged: bool,
        dry_run: bool,
    ) -> tuple[str, list[Chunk] | None, dict[str, Any]]:
        extra: dict[str, Any] = {}
        payload = self.client.get_country_overview(api_slug)
        document = extract_country_overview_from_api(payload, source_url)
        doc_sha1 = sha1_text(document.model_dump_json())
        extra["sections"] = len(document.sections)
        extra["doc_sha1"] = doc_sha1

        self.settings.state_db.upsert_website_url(
            source_url, page_kind="country_overview"
        )

        if skip_unchanged and should_skip_post(
            self.settings.state_db.get_website_content_sha1(source_url),
            doc_sha1,
        ):
            logger.info("Skipping unchanged prodapi country %s", source_url)
            return "skipped", None, extra

        self.settings.artifacts.save_extracted(
            source_url, document.model_dump(), kind="website"
        )
        chunks = chunk_document(
            document,
            max_tokens=self.settings.setting("chunk", "max_tokens", default=400),
            overlap_tokens=self.settings.setting("chunk", "overlap_tokens", default=80),
            embedding_model=self.settings.setting(
                "embedding", "model", default="amazon.titan-embed-text-v2:0"
            ),
            source_type="website",
            page_kind="country_overview",
        )
        extra["chunks"] = len(chunks)

        deleted = 0
        if not dry_run:
            try:
                deleted = self.settings.vectorstore.delete_by_filter(
                    {"source_url": {"$eq": source_url}}
                )
            except Exception as exc:
                logger.warning("Purge skipped for %s: %s", source_url, exc)
            self.settings.state_db.mark_website_crawled(source_url, doc_sha1)
        extra["deleted"] = deleted
        return "processed", chunks, extra
