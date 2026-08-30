"""Ingest Arnifi micro-services + setup product details from public prodapi."""

from __future__ import annotations

from typing import Any

from app.config.settings import Settings
from app.models.schemas import Chunk
from app.services.ingestion.pipeline import chunk_document, sha1_text, should_skip_post
from app.services.ingestion.website_sections import (
    extract_micro_service_from_api,
    extract_setup_product_from_api,
)
from app.services.prodapi.client import (
    ProdapiClient,
    micro_service_page_url,
    setup_product_page_url,
)
from app.utils.helpers import canonicalize_url, get_logger

logger = get_logger(__name__)

# Prompt's 10 public service landings ΓåÆ prodapi type name
TEN_SERVICE_TYPES: dict[str, str] = {
    "Post-Setup Compliance": "Post Setup Compliance",
    "Visa Services": "Visa Services",
    "Attestation": "Attestation",
    "Accounting & Bookkeeping": "Accounting & Bookkeeping",
    "Legal Service": "Legal Services",
    "Product Registration": "Product Registration & Certification",
    "Liquidation": "Liquidation",
    "Banking Services": "Banking Services",
    "Will Drafting": "Will Drafting Services",
    "Product Insights": "Product Insights & Guides",
}

SERVICE_LANDING_URLS: dict[str, str] = {
    "Post Setup Compliance": "https://arnifi.com/services/post-setup-compliance",
    "Visa Services": "https://arnifi.com/services/visa-service",
    "Attestation": "https://arnifi.com/services/attestations",
    "Accounting & Bookkeeping": "https://arnifi.com/services/accounting",
    "Legal Services": "https://arnifi.com/services/legal-services",
    "Product Registration & Certification": "https://arnifi.com/services/product-registration",
    "Liquidation": "https://arnifi.com/services/liquidation",
    "Banking Services": "https://arnifi.com/services/banking-services",
    "Will Drafting Services": "https://arnifi.com/services/will-drafting",
    "Product Insights & Guides": "https://arnifi.com/business-guides",
}


class ProdapiServicesIndexer:
    """Fetch /micro-services and setup /product-pages ΓåÆ chunk ΓåÆ Pinecone."""

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
        service_type: str | None = None,
        include_setup_products: bool = True,
        ten_services_only: bool = False,
        full_catalog: bool = False,
        skip_unchanged: bool = True,
    ) -> dict[str, Any]:
        """
        Ingest micro-service details and/or country licence products.

        full_catalog=True:
          - every micro-service type (incl. Funds / Other), paginated
          - every licence product-pages row per country (UAE 78, KSA 6, ΓÇª)
        """
        all_chunks: list[Chunk] = []
        processed = skipped = failed = 0
        deleted = 0
        rows: list[dict[str, Any]] = []

        if full_catalog:
            ten_services_only = False
            include_setup_products = True

        types = self.client.list_service_types()
        if ten_services_only:
            types = [t for t in types if t in TEN_SERVICE_TYPES]
            include_setup_products = False
        if service_type:
            needle = service_type.strip().lower()
            types = [t for t in types if t.lower() == needle]
            if not types:
                raise RuntimeError(f"No service type matched --service-type={service_type!r}")

        jobs: list[tuple[str, str, str]] = []  # kind, type_or_country, slug_or_id
        for stype in types:
            try:
                items = self.client.list_micro_services(stype)
            except Exception as exc:
                logger.error("Failed listing micro-services %s: %s", stype, exc)
                failed += 1
                rows.append({"kind": "micro_list", "type": stype, "status": "failed", "error": str(exc)})
                continue
            for item in items:
                slug = str(item.get("slug") or "").strip()
                if not slug:
                    continue
                jobs.append(("micro", stype, slug))

        setup_ids: list[tuple[str, int]] = []
        if include_setup_products:
            try:
                countries = self.client.list_countries()
                for country in countries:
                    api_slug = str(country.get("slug") or "").strip()
                    country_id = country.get("id")
                    if full_catalog and country_id is not None:
                        # Full licence grid (not just Top Packages strip)
                        try:
                            licences = self.client.list_licence_packages_for_country(
                                country_id
                            )
                        except Exception as exc:
                            logger.error(
                                "Failed listing licences for %s: %s", api_slug, exc
                            )
                            licences = []
                        for row in licences:
                            attrs = (
                                row.get("attributes")
                                if isinstance(row.get("attributes"), dict)
                                else row
                            )
                            pid = row.get("id") if isinstance(row, dict) else None
                            if pid is None and isinstance(attrs, dict):
                                pid = attrs.get("id")
                            if pid is None:
                                continue
                            pkg_slug = ""
                            if isinstance(attrs, dict):
                                pkg_slug = str(attrs.get("slug") or "").strip()
                            setup_ids.append((pkg_slug or api_slug, int(pid)))
                    else:
                        overview = self.client.get_country_overview(api_slug)
                        for pkg in overview.get("productPages") or []:
                            if not isinstance(pkg, dict) or pkg.get("id") is None:
                                continue
                            setup_ids.append(
                                (
                                    str(pkg.get("slug") or api_slug),
                                    int(pkg["id"]),
                                )
                            )
            except Exception as exc:
                logger.error("Failed collecting setup product ids: %s", exc)

        # Dedupe setup ids
        seen_ids: set[int] = set()
        for slug, pid in setup_ids:
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            jobs.append(("setup", slug, str(pid)))

        if limit is not None:
            jobs = jobs[:limit]

        logger.info(
            "prodapi ingest jobs=%d (micro+setup) full_catalog=%s ten_services=%s",
            len(jobs),
            full_catalog,
            ten_services_only,
        )

        for kind, type_or_slug, slug_or_id in jobs:
            row: dict[str, Any] = {"kind": kind, "ref": f"{type_or_slug}/{slug_or_id}"}
            try:
                if kind == "micro":
                    status, chunks, extra = self._index_micro(
                        service_type=type_or_slug,
                        slug=slug_or_id,
                        skip_unchanged=skip_unchanged,
                        dry_run=dry_run,
                    )
                else:
                    status, chunks, extra = self._index_setup(
                        product_id=int(slug_or_id),
                        fallback_slug=type_or_slug,
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
                logger.error("prodapi service ingest failed %s: %s", row["ref"], exc)
                row["status"] = "failed"
                row["error"] = str(exc)
                failed += 1
            rows.append(row)

        upserted = 0
        if all_chunks and not dry_run:
            vectors = self.settings.embedder.embed_texts([c.embed_text for c in all_chunks])
            upserted = self.settings.vectorstore.upsert_chunks(all_chunks, vectors)

        result: dict[str, Any] = {
            "jobs": len(jobs),
            "pages_processed": processed,
            "pages_skipped": skipped,
            "pages_failed": failed,
            "chunks_prepared": len(all_chunks),
            "chunks_upserted": upserted,
            "source_urls_purged": deleted,
            "dry_run": dry_run,
            "full_catalog": full_catalog,
            "items": rows[:200],
        }
        if not dry_run and all_chunks:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        return result

    def _index_setup(
        self,
        *,
        product_id: int,
        fallback_slug: str,
        skip_unchanged: bool,
        dry_run: bool,
        product_type: str | None = None,
        discovered_via: str | None = None,
        listing_url: str | None = None,
        force_lineage_refresh: bool = False,
    ) -> tuple[str, list[Chunk] | None, dict[str, Any]]:
        row = self.client.get_product_page_by_id(product_id)
        if not row:
            return "failed", None, {"error": f"product id {product_id} not found"}
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else row
        slug = str(attrs.get("slug") or fallback_slug)
        source_url = canonicalize_url(
            setup_product_page_url(slug, product_id=product_id)
        )
        document = extract_setup_product_from_api(row, source_url)
        return self._finalize(
            source_url=source_url,
            document=document,
            page_kind="product_detail",
            skip_unchanged=skip_unchanged,
            dry_run=dry_run,
            listing_url=listing_url,
            product_type=product_type,
            discovered_via=discovered_via,
            force_lineage_refresh=force_lineage_refresh,
            extra={"product_id": product_id, "slug": slug},
        )

    def _index_micro(
        self,
        *,
        service_type: str,
        slug: str,
        skip_unchanged: bool,
        dry_run: bool,
        product_type: str | None = None,
        discovered_via: str | None = None,
        listing_url: str | None = None,
        force_lineage_refresh: bool = False,
    ) -> tuple[str, list[Chunk] | None, dict[str, Any]]:
        payload = self.client.get_micro_service_detail(service_type, slug)
        service_id = payload.get("id")
        source_url = canonicalize_url(
            micro_service_page_url(service_type, slug, service_id=service_id)
        )
        document = extract_micro_service_from_api(payload, source_url)
        # Prefer prompt-facing service label + landing URL for hierarchy metadata.
        display = TEN_SERVICE_TYPES.get(service_type, service_type)
        document.category_name = display
        document.category_url = SERVICE_LANDING_URLS.get(display) or listing_url
        return self._finalize(
            source_url=source_url,
            document=document,
            page_kind="product_detail",
            skip_unchanged=skip_unchanged,
            dry_run=dry_run,
            listing_url=listing_url or document.category_url,
            product_type=product_type,
            discovered_via=discovered_via,
            force_lineage_refresh=force_lineage_refresh,
            extra={"service_type": service_type, "slug": slug, "service": display},
        )

    def ingest_company_fund_catalogs(
        self,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        licence_only: bool = False,
        funds_only: bool = False,
        guides_only: bool = False,
        skip_unchanged: bool = True,
        allow_partial: bool = False,
        country_id: int | str | None = None,
    ) -> dict[str, Any]:
        """
        Ingest Company/Licence + Fund + Business Guide catalogs from marketing entry points.

        Completeness: discovered detail_url count must equal live page stated total
        unless allow_partial=True.
        """
        from app.services.ingestion.company_fund_catalog import (
            DISCOVERED_VIA_FUNDS,
            DISCOVERED_VIA_GUIDES,
            DISCOVERED_VIA_LICENCE,
            FUNDS_LISTING_URL,
            GUIDES_LISTING_URL,
            GUIDES_SERVICE_TYPE,
            LICENCE_LISTING_URL,
            discover_funds_catalog,
            discover_guides_catalog,
            discover_licence_catalog,
        )

        exclusive = sum(bool(x) for x in (licence_only, funds_only, guides_only))
        if exclusive > 1:
            return {
                "aborted": True,
                "reason": "conflicting_flags",
                "error": "Pass at most one of licence_only / funds_only / guides_only",
            }

        catalogs: list[dict[str, Any]] = []
        if guides_only:
            catalogs.append(discover_guides_catalog(self.client))
        elif licence_only:
            catalogs.append(
                discover_licence_catalog(self.client, country_id=country_id)
            )
        elif funds_only:
            catalogs.append(discover_funds_catalog(self.client))
        else:
            catalogs.append(
                discover_licence_catalog(self.client, country_id=country_id)
            )
            catalogs.append(discover_funds_catalog(self.client))
            catalogs.append(discover_guides_catalog(self.client))

        incomplete = [c for c in catalogs if not c.get("complete")]
        if incomplete and not allow_partial:
            return {
                "aborted": True,
                "reason": "completeness_gate",
                "discovery": "prodapi",
                "catalogs": [
                    {
                        "product_type": c["product_type"],
                        "listing_url": c["listing_url"],
                        "live_stated_total": c.get("live_stated_total"),
                        "discovered": c.get("discovered"),
                        "complete": c.get("complete"),
                        "detail_urls": c.get("detail_urls", [])[:20],
                    }
                    for c in catalogs
                ],
            }

        jobs: list[dict[str, Any]] = []
        for cat in catalogs:
            for item in cat.get("items") or []:
                jobs.append(item)
        if limit is not None:
            jobs = jobs[:limit]

        all_chunks: list[Chunk] = []
        new_created = 0
        existing_matched_updated = 0
        failed = 0
        skipped = 0
        failed_detail_urls: list[dict[str, str]] = []
        rows: list[dict[str, Any]] = []

        for item in jobs:
            ptype = str(item.get("product_type") or "")
            via = str(item.get("discovered_via") or "")
            detail_url = str(item.get("detail_url") or "")
            row: dict[str, Any] = {
                "product_type": ptype,
                "detail_url": detail_url,
                "slug": item.get("slug"),
            }
            try:
                # Extract/chunk only first ΓÇö persist after the full batch succeeds
                # so a mid-run failure cannot purge Pinecone without a replacement upsert.
                if ptype == "licence":
                    pid = item.get("product_id")
                    if pid is None:
                        raise RuntimeError("licence row missing product_id")
                    status, chunks, extra = self._index_setup(
                        product_id=int(pid),
                        fallback_slug=str(item.get("slug") or "package"),
                        skip_unchanged=skip_unchanged,
                        dry_run=True,
                        product_type="licence",
                        discovered_via=via or DISCOVERED_VIA_LICENCE,
                        listing_url=LICENCE_LISTING_URL,
                        force_lineage_refresh=True,
                    )
                elif ptype == "fund":
                    slug = str(item.get("slug") or "").strip()
                    if not slug:
                        raise RuntimeError("fund row missing slug")
                    status, chunks, extra = self._index_micro(
                        service_type="Funds",
                        slug=slug,
                        skip_unchanged=skip_unchanged,
                        dry_run=True,
                        product_type="fund",
                        discovered_via=via or DISCOVERED_VIA_FUNDS,
                        listing_url=FUNDS_LISTING_URL,
                        force_lineage_refresh=True,
                    )
                elif ptype == "guide":
                    slug = str(item.get("slug") or "").strip()
                    if not slug:
                        raise RuntimeError("guide row missing slug")
                    status, chunks, extra = self._index_micro(
                        service_type=str(
                            item.get("service_type") or GUIDES_SERVICE_TYPE
                        ),
                        slug=slug,
                        skip_unchanged=skip_unchanged,
                        dry_run=True,
                        product_type="guide",
                        discovered_via=via or DISCOVERED_VIA_GUIDES,
                        listing_url=GUIDES_LISTING_URL,
                        force_lineage_refresh=True,
                    )
                else:
                    raise RuntimeError(f"unknown product_type={ptype!r}")

                row.update(extra)
                row["status"] = status
                if status == "processed" and chunks:
                    all_chunks.extend(chunks)
                    if extra.get("lineage_refresh") or extra.get("was_existing"):
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
                logger.error(
                    "company/fund/guide ingest failed %s: %s", detail_url, exc
                )
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
                "discovery": "prodapi",
                "new_created": new_created,
                "existing_matched_updated": existing_matched_updated,
                "pages_skipped": skipped,
                "pages_failed": failed,
                "failed_detail_urls": failed_detail_urls,
                "catalogs": [
                    {
                        "product_type": c["product_type"],
                        "live_stated_total": c.get("live_stated_total"),
                        "discovered": c.get("discovered"),
                        "complete": c.get("complete"),
                    }
                    for c in catalogs
                ],
                "items": rows[:200],
            }

        upserted = 0
        deleted = 0
        if all_chunks and not dry_run:
            # Purge + upsert only after a clean batch
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
            "discovery": "prodapi",
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
            "catalogs": [
                {
                    "product_type": c["product_type"],
                    "listing_url": c["listing_url"],
                    "discovery": c.get("discovery"),
                    "live_stated_total": c.get("live_stated_total"),
                    "discovered": c.get("discovered"),
                    "complete": c.get("complete"),
                }
                for c in catalogs
            ],
            "items": rows[:200],
        }
        if not dry_run and all_chunks:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        return result

    def _finalize(
        self,
        *,
        source_url: str,
        document: Any,
        page_kind: str,
        skip_unchanged: bool,
        dry_run: bool,
        extra: dict[str, Any],
        listing_url: str | None = None,
        product_type: str | None = None,
        discovered_via: str | None = None,
        force_lineage_refresh: bool = False,
    ) -> tuple[str, list[Chunk] | None, dict[str, Any]]:
        info = dict(extra)
        doc_sha1 = sha1_text(document.model_dump_json())
        info["sections"] = len(document.sections)
        info["doc_sha1"] = doc_sha1
        info["source_url"] = source_url
        if product_type:
            info["product_type"] = product_type
        if discovered_via:
            info["discovered_via"] = discovered_via

        prior_sha1 = self.settings.state_db.get_website_content_sha1(source_url)
        was_existing = bool(prior_sha1)
        info["was_existing"] = was_existing

        self.settings.state_db.upsert_website_url(source_url, page_kind=page_kind)
        content_unchanged = skip_unchanged and should_skip_post(prior_sha1, doc_sha1)
        # Content unchanged: skip re-embed unless we must stamp lineage metadata.
        if content_unchanged and not (
            force_lineage_refresh and (product_type or discovered_via)
        ):
            logger.info("Skipping unchanged prodapi service %s", source_url)
            return "skipped", None, info
        if content_unchanged and force_lineage_refresh:
            info["lineage_refresh"] = True

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
            page_kind=page_kind,
            listing_url=listing_url or document.category_url,
            product_type=product_type,
            discovered_via=discovered_via,
        )
        info["chunks"] = len(chunks)
        if chunks:
            info["catalog_service"] = chunks[0].catalog_service
            info["catalog_package"] = chunks[0].catalog_package

        deleted = 0
        if not dry_run:
            try:
                deleted = self.settings.vectorstore.delete_by_filter(
                    {"source_url": {"$eq": source_url}}
                )
            except Exception as exc:
                logger.warning("Purge skipped for %s: %s", source_url, exc)
            self.settings.state_db.mark_website_crawled(source_url, doc_sha1)
        info["deleted"] = deleted
        return "processed", chunks, info
