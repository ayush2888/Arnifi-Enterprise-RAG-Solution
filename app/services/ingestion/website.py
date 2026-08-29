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
    chunk_document,
    extract_document,
    sha1_text,
    should_skip_post,
)
from app.utils.helpers import (
    canonicalize_url,
    clean_text,
    get_logger,
    infer_page_kind,
    is_allowed_url,
    is_case_study_url,
    is_denied_website_url,
    is_product_detail_url,
    is_website_hub_url,
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
    """Parse a catalog page: cards + generic article-like text."""
    source_url = canonicalize_url(source_url)
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
) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = canonicalize_url(urljoin(page_url, href))
        if not is_allowed_url(absolute, allowed_domains):
            continue
        if is_denied_website_url(absolute, denylist_prefixes):
            continue
        if is_case_study_url(absolute) or is_product_detail_url(absolute):
            found.add(absolute)
            continue
        path = urlparse(absolute).path.rstrip("/")
        if path.startswith("/country-overview/") and path.count("/") == 2:
            found.add(absolute)
        elif path.startswith("/services/overview/") and path.count("/") == 3:
            found.add(absolute)
        elif is_website_hub_url(absolute) and absolute != canonicalize_url(page_url):
            # Do not BFS every hub from every hub — only overviews/country/case studies.
            continue
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
            self.settings.setting("crawl", "website", "max_child_pages", default=200) or 200
        )

        discovered = 0
        children = 0
        failed = 0
        child_urls: set[str] = set()

        for raw in seeds:
            url = canonicalize_url(str(raw))
            if is_denied_website_url(url, denylist):
                logger.info("Skipping denied seed %s", url)
                continue
            kind = infer_page_kind(url)
            if self.settings.state_db.upsert_website_url(url, page_kind=kind):
                discovered += 1
            try:
                result = self.settings.fetcher.fetch(url)
                body = result["html"]
                self.settings.artifacts.save_html("website", url, body)
                if urlparse(url).path.endswith("llms.txt"):
                    continue
                for child in extract_child_urls(
                    body,
                    url,
                    allowed_domains=list(allowed),
                    denylist_prefixes=list(denylist),
                ):
                    if len(child_urls) >= max_children:
                        break
                    child_urls.add(child)
                    ck = infer_page_kind(child)
                    if self.settings.state_db.upsert_website_url(
                        child, listing_url=url, page_kind=ck
                    ):
                        children += 1
            except Exception as exc:
                failed += 1
                logger.error("Website crawl failed %s: %s", url, exc)

        return {
            "seeds": len(seeds),
            "new_hubs": discovered,
            "new_children": children,
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
    ) -> dict[str, Any]:
        urls = self.settings.state_db.get_all_website_urls()
        if limit:
            urls = urls[:limit]

        all_chunks: list[Chunk] = []
        processed = skipped = failed = 0

        for url in urls:
            status, chunks = self._index_one(url, skip_unchanged=skip_unchanged)
            if status == "processed" and chunks:
                all_chunks.extend(chunks)
                processed += 1
            elif status == "skipped":
                skipped += 1
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
            "chunks_prepared": len(all_chunks),
            "chunks_upserted": upserted,
            "dry_run": dry_run,
        }
        if not dry_run and all_chunks:
            result["index_stats"] = self.settings.vectorstore.describe_stats()
        return result

    def _index_one(
        self, url: str, *, skip_unchanged: bool
    ) -> tuple[str, list[Chunk] | None]:
        try:
            result = self.settings.fetcher.fetch(url)
            body = result["html"]
            self.settings.artifacts.save_html("website", url, body)
            page_kind = self.settings.state_db.get_website_page_kind(url) or infer_page_kind(url)
            if urlparse(url).path.endswith("llms.txt") or "llms.txt" in url:
                document = extract_llms_document(body, url)
            else:
                document = extract_website_document(body, url, page_kind)
            doc_sha1 = sha1_text(document.model_dump_json())
            if skip_unchanged and should_skip_post(
                self.settings.state_db.get_website_content_sha1(url),
                doc_sha1,
            ):
                logger.info("Skipping unchanged website page %s", url)
                return "skipped", None

            self.settings.artifacts.save_extracted(
                url, document.model_dump(), kind="website"
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
            )
            self.settings.state_db.mark_website_crawled(url, doc_sha1)
            return "processed", chunks
        except Exception as exc:
            logger.error("Website ingest failed %s: %s", url, exc)
            return "failed", None
