"""Isolated public-catalog ingest for arnifi.com (not blogs).

Blog crawl still uses ListingCrawler + post_urls.
This module writes website_urls and source_type=website.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.models.schemas import Chunk, Document, Section
from app.services.ingestion.pipeline import (
    _sitemap_locs,
    chunk_document,
    extract_document,
    sha1_text,
    should_skip_post,
)
from app.services.ingestion.website_sections import (
    country_catalog_detail_urls,
    extract_country_overview_document,
    extract_package_detail_document,
    missing_package_sections,
)
from app.utils.helpers import (
    canonicalize_url,
    clean_text,
    get_logger,
    infer_page_kind,
    is_allowed_url,
    is_case_study_url,
    is_country_overview_url,
    is_denied_website_url,
    is_homepage_url,
    is_product_detail_url,
    is_service_landing_url,
    is_service_package_url,
    is_website_hub_url,
    should_skip_website_ingest,
)

logger = get_logger(__name__)

_PRICE_RE = re.compile(
    r"(AED|SAR|USD|SGD|EUR|GBP)\s*[\d,]+(?:\.\d+)?",
    re.I,
)
_STARTING_RE = re.compile(r"starting from", re.I)


def extract_llms_document(text: str, source_url: str) -> Document:
    """Split llms.txt on markdown headings."""
    source_url = canonicalize_url(source_url)
    lines = text.replace("\r\n", "\n").split("\n")
    title = "Arnifi website facts"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            title = clean_text(stripped[2:])
            break

    sections: list[Section] = []
    current_heading = "Overview"
    parts: list[str] = []

    def flush() -> None:
        body = clean_text("\n".join(parts))
        if not body:
            return
        key = hashlib.sha1(f"{source_url}|{current_heading}|{body[:80]}".encode()).hexdigest()[:16]
        sections.append(
            Section(
                section_id=key,
                heading_path=[current_heading],
                heading_text=current_heading,
                text=body,
            )
        )

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            flush()
            current_heading = clean_text(stripped[3:])
            parts = []
            continue
        if stripped.startswith("# "):
            continue
        parts.append(line)
    flush()

    if not sections:
        body = clean_text(text)
        sections.append(
            Section(
                section_id=hashlib.sha1(source_url.encode()).hexdigest()[:16],
                heading_path=["Overview"],
                heading_text="Overview",
                text=body,
            )
        )

    return Document(
        title=title,
        source_url=source_url,
        source_domain="arnifi.com",
        category_name="company",
        sections=sections,
    )


def _card_container(heading: Tag) -> Tag | None:
    node: Tag | None = heading
    for _ in range(10):
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        if _STARTING_RE.search(text) and _PRICE_RE.search(text) and 40 < len(text) < 2500:
            return node
        node = node.parent if isinstance(node.parent, Tag) else None
    return heading.find_parent("div")


def extract_service_cards(html: str, source_url: str) -> list[Section]:
    soup = BeautifulSoup(html, "lxml")
    sections: list[Section] = []
    seen: set[str] = set()

    def add_card(title: str, body: str) -> None:
        title = clean_text(title)
        body = clean_text(body)
        if len(title) < 8 or title in seen or not _PRICE_RE.search(body):
            return
        seen.add(title)
        key = hashlib.sha1(f"{source_url}|card|{title}".encode()).hexdigest()[:16]
        sections.append(
            Section(
                section_id=key,
                heading_path=["Catalog", title],
                heading_text=title,
                text=body,
            )
        )

    for heading in soup.find_all(["h2", "h3"]):
        title = heading.get("aria-label") or heading.get_text(" ", strip=True)
        container = _card_container(heading)
        if container is not None:
            add_card(title, container.get_text(" ", strip=True))

    for node in soup.find_all(string=_STARTING_RE):
        parent = node.parent
        if not isinstance(parent, Tag):
            continue
        block: Tag | None = parent
        for _ in range(8):
            if block is None:
                break
            text = block.get_text(" ", strip=True)
            if _PRICE_RE.search(text) and 20 < len(text) < 2500:
                heading = block.find(["h2", "h3", "h4"])
                title = ""
                if heading:
                    title = heading.get("aria-label") or heading.get_text(" ", strip=True)
                if not title:
                    paras = [clean_text(p.get_text(" ", strip=True)) for p in block.find_all("p")]
                    title = next((p for p in paras if 12 < len(p) < 120 and "starting" not in p.lower()), "")
                if title:
                    add_card(title, text)
                break
            block = block.parent if isinstance(block.parent, Tag) else None

    return sections


def extract_website_document(html: str, source_url: str, page_kind: str) -> Document:
    """Parse a catalog page: section-aware where we know the layout."""
    source_url = canonicalize_url(source_url)
    if page_kind == "country_overview":
        return extract_country_overview_document(html, source_url)
    if page_kind in {"service_package", "product_detail"}:
        document, _missing = extract_package_detail_document(html, source_url, page_kind)
        return document

    base = extract_document(html, source_url)
    cards = extract_service_cards(html, source_url)

    merged: list[Section] = list(cards)
    existing = {s.heading_text.lower() for s in merged}
    for section in base.sections:
        if section.heading_text.lower() in existing:
            continue
        if len(section.text) < 40:
            continue
        merged.append(section)

    title = base.title
    if not merged:
        soup = BeautifulSoup(html, "lxml")
        main = soup.find("main") or soup.body or soup
        text = clean_text(main.get_text("\n", strip=True))[:8000]
        if text:
            merged.append(
                Section(
                    section_id=hashlib.sha1(source_url.encode()).hexdigest()[:16],
                    heading_path=[page_kind],
                    heading_text=page_kind,
                    text=text,
                )
            )

    return Document(
        title=title,
        author=base.author,
        published_at=base.published_at,
        source_url=source_url,
        source_domain="arnifi.com",
        category_name=page_kind,
        sections=merged,
    )


def extract_child_urls(
    html: str,
    page_url: str,
    *,
    allowed_domains: list[str],
    denylist_prefixes: list[str] | None = None,
    collect_hubs: bool = False,
) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    page_canon = canonicalize_url(page_url)
    from_home = collect_hubs or is_homepage_url(page_url)
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = canonicalize_url(urljoin(page_url, href))
        if absolute == page_canon:
            continue
        if not is_allowed_url(absolute, allowed_domains):
            continue
        if is_denied_website_url(absolute, denylist_prefixes):
            continue
        if is_case_study_url(absolute) or is_product_detail_url(absolute):
            found.add(absolute)
            continue
        if is_country_overview_url(absolute) or is_service_package_url(absolute):
            found.add(absolute)
            continue
        if is_service_landing_url(absolute) or (
            from_home and is_website_hub_url(absolute)
        ):
            found.add(absolute)
            continue
        path = urlparse(absolute).path.rstrip("/")
        if path.startswith("/services/overview/") and path.count("/") == 3:
            found.add(absolute)

    # Country pages embed Top Funds / packages in RSC (no <a href> on cards).
    if is_country_overview_url(page_url) or is_homepage_url(page_url):
        for child in country_catalog_detail_urls(html, page_url):
            if is_denied_website_url(child, denylist_prefixes):
                continue
            if is_allowed_url(child, allowed_domains):
                found.add(child)
    return found


def discover_catalog_urls_from_sitemap(
    fetcher: Any,
    sitemap_index_url: str,
    allowed_domains: list[str],
    denylist_prefixes: list[str] | None = None,
) -> set[str]:
    """Collect product-detail and service-package URLs from the public sitemap."""
    found: set[str] = set()
    try:
        index_html = fetcher.fetch(sitemap_index_url)["html"]
    except Exception as exc:
        logger.warning("Catalog sitemap index failed %s: %s", sitemap_index_url, exc)
        return found

    child_maps = _sitemap_locs(index_html)
    targets = child_maps or [sitemap_index_url]
    for map_url in targets:
        try:
            body = fetcher.fetch(map_url)["html"]
        except Exception as exc:
            logger.warning("Catalog sitemap fetch failed %s: %s", map_url, exc)
            continue
        for raw in _sitemap_locs(body):
            absolute = canonicalize_url(raw)
            if not is_allowed_url(absolute, allowed_domains):
                continue
            if is_denied_website_url(absolute, denylist_prefixes):
                continue
            if is_product_detail_url(absolute) or is_service_package_url(absolute):
                found.add(absolute)
            elif is_country_overview_url(absolute) or is_service_landing_url(absolute):
                found.add(absolute)
    return found


class WebsiteCrawler:
    """Fetch allowlisted hubs and register child catalog URLs in SQLite."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def crawl(self) -> dict[str, Any]:
        seeds = self.settings.setting("seeds", "website_pages", default=[]) or []
        allowed = self.settings.setting("crawl", "allowed_domains", default=["arnifi.com"])
        denylist = self.settings.setting("crawl", "website", "denylist_prefixes", default=[]) or []
        max_children = int(
            self.settings.setting("crawl", "website", "max_child_pages", default=2000) or 2000
        )
        sitemap_url = str(
            self.settings.setting(
                "seeds",
                "website_sitemap_index",
                default=self.settings.setting("seeds", "blog_sitemap_index", default=""),
            )
            or ""
        )

        discovered = 0
        children = 0
        failed = 0
        child_urls: set[str] = set()
        seen: set[str] = set()
        # BFS: seeds (depth 0) ΓåÆ listing/detail links (depth 1) ΓåÆ related links (depth 2).
        # Detail HTML is still a shell for section bodies; prodapi owns content.
        # Depth 2 is enough to pick up related-package links from product pages.
        max_depth = int(
            self.settings.setting("crawl", "website", "max_depth", default=2) or 2
        )
        queue: list[tuple[str, int, str | None]] = []  # url, depth, parent

        def register(url: str, *, listing_url: str | None = None) -> None:
            nonlocal discovered, children
            kind = infer_page_kind(url)
            is_new = self.settings.state_db.upsert_website_url(
                url, listing_url=listing_url, page_kind=kind
            )
            if is_new:
                if listing_url:
                    children += 1
                else:
                    discovered += 1

        for raw in seeds:
            url = canonicalize_url(str(raw))
            if is_denied_website_url(url, denylist):
                logger.info("Skipping denied seed %s", url)
                continue
            register(url)
            queue.append((url, 0, None))

        listing_stats: dict[str, dict[str, int]] = {}

        while queue:
            url, depth, parent = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                result = self.settings.fetcher.fetch(url)
                if int(result.get("status_code") or 0) >= 400:
                    failed += 1
                    logger.error("Website crawl HTTP %s for %s", result.get("status_code"), url)
                    continue
                body = result["html"]
                final_url = canonicalize_url(str(result.get("final_url") or url))
                if final_url != url:
                    register(final_url, listing_url=url)
                    url = final_url
                    seen.add(url)
                self.settings.artifacts.save_html("website", url, body)
                if urlparse(url).path.endswith("llms.txt"):
                    continue
                found = extract_child_urls(
                    body,
                    url,
                    allowed_domains=list(allowed),
                    denylist_prefixes=list(denylist),
                    collect_hubs=is_homepage_url(url),
                )
                if parent is None and (
                    is_country_overview_url(url) or is_service_landing_url(url)
                ):
                    listing_stats[url] = {
                        "discovered_children": len(found),
                        "depth": depth,
                    }
                if depth >= max_depth:
                    continue
                for child in found:
                    if len(child_urls) >= max_children:
                        break
                    if child in seen:
                        continue
                    child_urls.add(child)
                    register(child, listing_url=url)
                    queue.append((child, depth + 1, url))
            except Exception as exc:
                failed += 1
                logger.error("Website crawl failed %s: %s", url, exc)

        sitemap_added = 0
        if sitemap_url:
            for child in discover_catalog_urls_from_sitemap(
                self.settings.fetcher,
                sitemap_url,
                list(allowed),
                list(denylist),
            ):
                if child not in child_urls and len(child_urls) >= max_children:
                    break
                before = children
                register(child)
                if children > before or discovered:
                    sitemap_added += 1
                    child_urls.add(child)

        # Surface listing pages that discovered zero package/detail children.
        empty_listings = [
            u
            for u, st in listing_stats.items()
            if st.get("discovered_children", 0) == 0
        ]
        if empty_listings:
            logger.warning(
                "Website crawl: %d listing seed(s) discovered 0 child links "
                "(likely Next.js shell ΓÇö use prodapi for package bodies): %s",
                len(empty_listings),
                empty_listings[:8],
            )

        return {
            "seeds": len(seeds),
            "new_hubs": discovered,
            "new_children": children,
            "pages_fetched": len(seen),
            "max_depth": max_depth,
            "listing_stats": listing_stats,
            "empty_listings": empty_listings,
            "sitemap_urls_seen": sitemap_added,
            "failed": failed,
            "state": self.settings.state_db.stats(),
        }


class WebsiteIndexer:
    """Chunk + embed public catalog pages (source_type=website)."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def ingest(
        self,
        *,
        limit: int | None = None,
        skip_unchanged: bool = True,
        dry_run: bool = False,
        page_kind: str | None = None,
        prune_gone: bool = True,
    ) -> dict[str, Any]:
        urls = self.settings.state_db.get_all_website_urls(page_kind=page_kind)
        if limit:
            urls = urls[:limit]

        all_chunks: list[Chunk] = []
        processed = skipped = failed = 0
        pruned = 0
        missing_sections: list[dict[str, Any]] = []

        for url in urls:
            status, chunks, extra = self._index_one(url, skip_unchanged=skip_unchanged)
            if extra.get("missing_sections"):
                missing_sections.append(
                    {"url": url, "missing": extra["missing_sections"]}
                )
            if status == "processed" and chunks:
                all_chunks.extend(chunks)
                processed += 1
            elif status == "skipped":
                skipped += 1
            elif status == "gone":
                pruned += 1
                if prune_gone and not dry_run:
                    try:
                        self.settings.vectorstore.delete_by_filter(
                            {"source_url": {"$eq": url}}
                        )
                    except Exception as exc:
                        logger.warning(
                            "Purge skipped for gone URL %s: %s", url, exc
                        )
            else:
                failed += 1

        upserted = 0
        if all_chunks and not dry_run:
            vectors = self.settings.embedder.embed_texts([c.embed_text for c in all_chunks])
            upserted = self.settings.vectorstore.upsert_chunks(all_chunks, vectors)

        result: dict[str, Any] = {
            "pages_processed": processed,
            "pages_skipped": skipped,
            "pages_failed": failed,
            "pages_gone_purged": pruned,
            "chunks_prepared": len(all_chunks),
            "chunks_upserted": upserted,
            "page_kind": page_kind,
            "dry_run": dry_run,
            "sections_missing": missing_sections[:50],
        }
        if not dry_run and all_chunks:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        return result

    def _index_one(
        self, url: str, *, skip_unchanged: bool
    ) -> tuple[str, list[Chunk] | None, dict[str, Any]]:
        extra: dict[str, Any] = {}
        try:
            page_kind = self.settings.state_db.get_website_page_kind(url) or infer_page_kind(url)
            if should_skip_website_ingest(url, page_kind):
                logger.info("Skipping non-RAG catalog URL %s kind=%s", url, page_kind)
                return "skipped", None, extra

            result = self.settings.fetcher.fetch(url)
            status_code = int(result.get("status_code") or 0)
            if status_code in {404, 410}:
                self.settings.state_db.mark_website_gone(url)
                logger.info("Website page gone HTTP %s %s", status_code, url)
                return "gone", None, extra
            if status_code >= 400:
                raise RuntimeError(f"HTTP {status_code}")

            body = result["html"]
            ingest_url = canonicalize_url(str(result.get("final_url") or url))
            self.settings.artifacts.save_html("website", ingest_url, body)
            page_kind = infer_page_kind(ingest_url) or page_kind
            if should_skip_website_ingest(ingest_url, page_kind):
                return "skipped", None, extra

            if urlparse(ingest_url).path.endswith("llms.txt") or "llms.txt" in ingest_url:
                document = extract_llms_document(body, ingest_url)
            else:
                document = extract_website_document(body, ingest_url, page_kind)
                if page_kind in {"service_package", "product_detail"}:
                    extra["missing_sections"] = missing_package_sections(document)

            doc_sha1 = sha1_text(document.model_dump_json())
            if skip_unchanged and should_skip_post(
                self.settings.state_db.get_website_content_sha1(url)
                or self.settings.state_db.get_website_content_sha1(ingest_url),
                doc_sha1,
            ):
                logger.info("Skipping unchanged website page %s", ingest_url)
                return "skipped", None, extra

            self.settings.artifacts.save_extracted(
                ingest_url, document.model_dump(), kind="website"
            )
            listing_url = self.settings.state_db.get_website_listing_url(url)
            chunks = chunk_document(
                document,
                max_tokens=self.settings.setting("chunk", "max_tokens", default=400),
                overlap_tokens=self.settings.setting("chunk", "overlap_tokens", default=80),
                embedding_model=self.settings.setting(
                    "embedding", "model", default="amazon.titan-embed-text-v2:0"
                ),
                listing_url=listing_url,
                source_type="website",
                page_kind=page_kind,
            )
            self.settings.state_db.mark_website_crawled(ingest_url, doc_sha1)
            if ingest_url != url:
                self.settings.state_db.mark_website_crawled(url, doc_sha1)
            return "processed", chunks, extra
        except Exception as exc:
            logger.error("Website ingest failed %s: %s", url, exc)
            return "failed", None, extra
