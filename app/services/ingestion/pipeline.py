"""
Offline indexing pipeline — read top to bottom to follow the data path.

  1. Discover post URLs from listing pages
  2. Fetch HTML
  3. Parse into a Document
  4. Chunk for retrieval
  5. Embed text (via Settings.embedder — Bedrock Titan)
  6. Upsert vectors to Pinecone
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urljoin, urlparse

import requests
import tiktoken
from bs4 import BeautifulSoup, Tag
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models.schemas import Block, Chunk, Document, Section
from app.utils.helpers import (
    canonicalize_url,
    clean_text,
    get_logger,
    is_blog_post_url,
    is_category_url,
    is_pagination_url,
    listing_base_url,
    WEBSITE_PRICE_PAGE_KINDS,
)

if TYPE_CHECKING:
    from app.config.settings import Settings

logger = get_logger(__name__)

PostStatus = Literal["processed", "skipped", "failed"]


# --- Dedup helpers ---


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def should_skip_post(existing_sha1: str | None, new_sha1: str) -> bool:
    # Skip re-embedding when the parsed document hasn't changed since last crawl.
    return existing_sha1 is not None and existing_sha1 == new_sha1


# --- Persistence: SQLite crawl registry + debug artifacts ---


class StateDB:
    # sqlite database to store the state of the crawl
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS listing_urls (
                url TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'discovered',
                discovered_at TEXT NOT NULL,
                last_crawled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS post_urls (
                url TEXT PRIMARY KEY,
                listing_url TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                discovered_at TEXT NOT NULL,
                last_crawled_at TEXT,
                content_sha1 TEXT
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                listing_count INTEGER DEFAULT 0,
                post_count INTEGER DEFAULT 0,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS wa_chats (
                chat_id TEXT PRIMARY KEY,
                chat_name TEXT,
                chat_type TEXT,
                last_synced_at TEXT,
                message_count INTEGER DEFAULT 0,
                artifact_path TEXT
            );

            CREATE TABLE IF NOT EXISTS wa_episodes (
                episode_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                content_sha1 TEXT,
                summary_sha1 TEXT,
                message_count INTEGER DEFAULT 0,
                started_at TEXT,
                ended_at TEXT,
                last_ingested_at TEXT,
                FOREIGN KEY (chat_id) REFERENCES wa_chats(chat_id)
            );

            CREATE TABLE IF NOT EXISTS website_urls (
                url TEXT PRIMARY KEY,
                listing_url TEXT,
                page_kind TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                discovered_at TEXT NOT NULL,
                last_crawled_at TEXT,
                content_sha1 TEXT
            );
            """
        )
        self._conn.commit()
        self._ensure_column("wa_chats", "invite_link", "TEXT")
        self._ensure_column("wa_chats", "invite_updated_at", "TEXT")

    def _ensure_column(self, table: str, column: str, typedef: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column not in existing:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {typedef}"
            )
            self._conn.commit()
     
     # Upsert listing URL is a function that inserts a new listing URL into the database if it doesn't exist, or updates it if it does
    def upsert_listing_url(self, url: str, status: str = "discovered") -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        cur.execute("SELECT url FROM listing_urls WHERE url = ?", (url,))
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(
                "UPDATE listing_urls SET status = ? WHERE url = ?",
                (status, url),
            )
        else:
            cur.execute(
                "INSERT INTO listing_urls (url, status, discovered_at) VALUES (?, ?, ?)",
                (url, status, now),
            )
        self._conn.commit()
        return not exists

# Mark listing crawled is a function that updates the status of a listing URL to crawled
    def mark_listing_crawled(self, url: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE listing_urls SET status = 'crawled', last_crawled_at = ? WHERE url = ?",
            (now, url),
        )
        self._conn.commit()

# Upsert post URL is a function that inserts a new post URL into the database if it doesn't exist, or updates it if it does
    def upsert_post_url(self, url: str, listing_url: str | None = None) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        cur.execute("SELECT url FROM post_urls WHERE url = ?", (url,))
        exists = cur.fetchone() is not None
        if exists:
            if listing_url:
                cur.execute(
                    "UPDATE post_urls SET listing_url = COALESCE(listing_url, ?) WHERE url = ?",
                    (listing_url, url),
                )
        else:
            cur.execute(
                "INSERT INTO post_urls (url, listing_url, status, discovered_at) VALUES (?, ?, 'discovered', ?)",
                (url, listing_url, now),
            )
        self._conn.commit()
        return not exists

    def mark_post_crawled(self, url: str, content_sha1: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE post_urls
            SET status = 'crawled', last_crawled_at = ?, content_sha1 = COALESCE(?, content_sha1)
            WHERE url = ?
            """,
            (now, content_sha1, url),
        )
        self._conn.commit()

    def get_all_post_urls(self, status: str | None = None) -> list[str]:
        if status:
            rows = self._conn.execute(
                "SELECT url FROM post_urls WHERE status = ? ORDER BY discovered_at",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT url FROM post_urls ORDER BY discovered_at"
            ).fetchall()
        return [row["url"] for row in rows]

    def get_post_content_sha1(self, url: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_sha1 FROM post_urls WHERE url = ?", (url,)
        ).fetchone()
        return row["content_sha1"] if row else None

    def stats(self) -> dict[str, Any]:
        listing_count = self._conn.execute("SELECT COUNT(*) AS c FROM listing_urls").fetchone()["c"]
        post_count = self._conn.execute("SELECT COUNT(*) AS c FROM post_urls").fetchone()["c"]
        crawled_posts = self._conn.execute(
            "SELECT COUNT(*) AS c FROM post_urls WHERE status = 'crawled'"
        ).fetchone()["c"]
        website_count = self._conn.execute("SELECT COUNT(*) AS c FROM website_urls").fetchone()["c"]
        crawled_website = self._conn.execute(
            "SELECT COUNT(*) AS c FROM website_urls WHERE status = 'crawled'"
        ).fetchone()["c"]
        return {
            "listing_urls": listing_count,
            "post_urls": post_count,
            "crawled_posts": crawled_posts,
            "website_urls": website_count,
            "crawled_website": crawled_website,
        }

    def upsert_website_url(
        self,
        url: str,
        *,
        listing_url: str | None = None,
        page_kind: str | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        cur.execute("SELECT url FROM website_urls WHERE url = ?", (url,))
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(
                """
                UPDATE website_urls
                SET listing_url = COALESCE(listing_url, ?),
                    page_kind = COALESCE(?, page_kind)
                WHERE url = ?
                """,
                (listing_url, page_kind, url),
            )
        else:
            cur.execute(
                """
                INSERT INTO website_urls (url, listing_url, page_kind, status, discovered_at)
                VALUES (?, ?, ?, 'discovered', ?)
                """,
                (url, listing_url, page_kind, now),
            )
        self._conn.commit()
        return not exists

    def mark_website_crawled(self, url: str, content_sha1: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE website_urls
            SET status = 'crawled', last_crawled_at = ?, content_sha1 = COALESCE(?, content_sha1)
            WHERE url = ?
            """,
            (now, content_sha1, url),
        )
        self._conn.commit()

    def get_all_website_urls(
        self, status: str | None = None, page_kind: str | None = None
    ) -> list[str]:
        sql = "SELECT url FROM website_urls WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if page_kind:
            sql += " AND page_kind = ?"
            params.append(page_kind)
        sql += " ORDER BY discovered_at"
        rows = self._conn.execute(sql, params).fetchall()
        return [row["url"] for row in rows]

    def get_website_content_sha1(self, url: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_sha1 FROM website_urls WHERE url = ?", (url,)
        ).fetchone()
        return row["content_sha1"] if row else None

    def get_website_page_kind(self, url: str) -> str | None:
        row = self._conn.execute(
            "SELECT page_kind FROM website_urls WHERE url = ?", (url,)
        ).fetchone()
        return row["page_kind"] if row else None

    def get_website_listing_url(self, url: str) -> str | None:
        row = self._conn.execute(
            "SELECT listing_url FROM website_urls WHERE url = ?", (url,)
        ).fetchone()
        if not row:
            return None
        value = row["listing_url"]
        return str(value) if value else None

    def mark_website_gone(self, url: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE website_urls
            SET status = 'gone', last_crawled_at = ?
            WHERE url = ?
            """,
            (now, url),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_wa_chat(
        self,
        chat_id: str,
        chat_name: str,
        chat_type: str,
        *,
        message_count: int = 0,
        artifact_path: str | None = None,
        invite_link: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        invite = (invite_link or "").strip() or None
        invite_updated = now if invite else None
        self._conn.execute(
            """
            INSERT INTO wa_chats (
                chat_id, chat_name, chat_type, last_synced_at,
                message_count, artifact_path, invite_link, invite_updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_name = excluded.chat_name,
                chat_type = excluded.chat_type,
                last_synced_at = excluded.last_synced_at,
                message_count = excluded.message_count,
                artifact_path = COALESCE(excluded.artifact_path, wa_chats.artifact_path),
                invite_link = COALESCE(excluded.invite_link, wa_chats.invite_link),
                invite_updated_at = COALESCE(
                    excluded.invite_updated_at, wa_chats.invite_updated_at
                )
            """,
            (
                chat_id,
                chat_name,
                chat_type,
                now,
                message_count,
                artifact_path,
                invite,
                invite_updated,
            ),
        )
        self._conn.commit()

    def get_wa_invite_link(self, chat_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT invite_link FROM wa_chats WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if not row:
            return None
        link = row["invite_link"]
        if not isinstance(link, str):
            return None
        link = link.strip()
        return link or None

    def set_wa_invite_link(self, chat_id: str, invite_link: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        invite = (invite_link or "").strip() or None
        self._conn.execute(
            """
            UPDATE wa_chats
            SET invite_link = ?, invite_updated_at = ?
            WHERE chat_id = ?
            """,
            (invite, now if invite else None, chat_id),
        )
        self._conn.commit()

    def get_wa_episode_sha1(self, episode_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT content_sha1 FROM wa_episodes WHERE episode_id = ?",
            (episode_id,),
        )
        row = cur.fetchone()
        return str(row["content_sha1"]) if row and row["content_sha1"] else None

    def upsert_wa_episode(
        self,
        episode_id: str,
        chat_id: str,
        *,
        content_sha1: str,
        summary_sha1: str | None,
        message_count: int,
        started_at: str | None,
        ended_at: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO wa_episodes (
                episode_id, chat_id, content_sha1, summary_sha1,
                message_count, started_at, ended_at, last_ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                content_sha1 = excluded.content_sha1,
                summary_sha1 = COALESCE(excluded.summary_sha1, wa_episodes.summary_sha1),
                message_count = excluded.message_count,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                last_ingested_at = excluded.last_ingested_at
            """,
            (
                episode_id,
                chat_id,
                content_sha1,
                summary_sha1,
                message_count,
                started_at,
                ended_at,
                now,
            ),
        )
        self._conn.commit()


class ArtifactStore:
    """Caches raw HTML and parsed JSON under data/artifacts/ for debugging and resume."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.list_pages_dir = self.base_dir / "list_pages"
        self.posts_dir = self.base_dir / "posts"
        self.extracted_dir = self.base_dir / "extracted"
        for d in (self.list_pages_dir, self.posts_dir, self.extracted_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _slug_from_url(self, url: str) -> str:
        # Keep paths short: Windows MAX_PATH (~260) breaks long announcement URLs
        # under OneDrive nested folders.
        slug = re.sub(r"^https?://", "", url.rstrip("/"))
        slug = re.sub(r"[^\w\-]+", "_", slug)
        if len(slug) <= 120:
            return slug
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        return f"{slug[:100]}_{digest}"

    def save_html(self, kind: str, url: str, html: str) -> Path:
        if kind == "listing":
            target_dir = self.list_pages_dir
        elif kind == "post":
            target_dir = self.posts_dir
        elif kind == "website":
            target_dir = self.base_dir / "website" / "html"
            target_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError(f"Unknown kind: {kind}")
        path = target_dir / f"{self._slug_from_url(url)}.html"
        path.write_text(html, encoding="utf-8")
        return path

    def save_extracted(self, url: str, document: dict[str, Any], *, kind: str = "post") -> Path:
        if kind == "website":
            target_dir = self.base_dir / "website" / "extracted"
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"{self._slug_from_url(url)}.json"
        else:
            path = self.extracted_dir / f"{self._slug_from_url(url)}.json"
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def load_extracted(self, url: str) -> dict[str, Any] | None:
        path = self.extracted_dir / f"{self._slug_from_url(url)}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


# --- Step 2: Fetch HTML ---

DEFAULT_HEADERS = {
    "User-Agent": (
        "ArnifiBlogRAGBot/1.0 (+https://arnifi.com; research indexing for internal RAG)"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8"
    ),
}


class Fetcher:
    def __init__(
        self,
        delay_seconds: float = 1.0,
        timeout_seconds: int = 30,
        max_retries: int = 3,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._last_fetch_at = 0.0

    def _polite_wait(self) -> None:
        elapsed = time.time() - self._last_fetch_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    @retry(
        # tenacity retries transient network errors without crashing the whole ingest run.
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def fetch(self, url: str) -> dict[str, Any]:
        self._polite_wait()
        logger.info("Fetching %s", url)
        response = self.session.get(url, timeout=self.timeout_seconds, allow_redirects=True)
        self._last_fetch_at = time.time()
        status = int(response.status_code)
        if status >= 500:
            response.raise_for_status()
        final_url = str(response.url or url)
        return {
            "url": url,
            "final_url": final_url,
            "status_code": status,
            "html": response.text,
            "etag": response.headers.get("ETag"),
            "content_type": response.headers.get("Content-Type", ""),
        }


# --- Step 1 (helper): sitemap discovery (complete blog inventory) ---


def _sitemap_locs(xml_text: str) -> list[str]:
    """Extract <loc> values from a sitemap or sitemap-index document."""
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, flags=re.I)


def discover_blog_urls_from_sitemap(
    fetcher: Fetcher,
    sitemap_index_url: str,
    allowed_domains: list[str],
) -> dict[str, set[str]]:
    """Read Arnifi sitemap index → child sitemaps → blog post + category URLs.

    Why: https://arnifi.com/blog/ uses Load More, and /blog/page/N returns the
    same featured posts every time. The public sitemap is the complete inventory.
    """
    post_urls: set[str] = set()
    category_urls: set[str] = set()

    try:
        index_html = fetcher.fetch(sitemap_index_url)["html"]
    except Exception as exc:
        logger.error("Failed to fetch sitemap index %s: %s", sitemap_index_url, exc)
        return {"post_urls": post_urls, "category_urls": category_urls}

    child_maps = _sitemap_locs(index_html)
    # Some hosts serve a single urlset at the index URL.
    targets = child_maps or [sitemap_index_url]

    for map_url in targets:
        canonical_map = canonicalize_url(map_url) if map_url.endswith(".xml") else map_url
        try:
            body = fetcher.fetch(canonical_map)["html"]
        except Exception as exc:
            logger.error("Failed to fetch sitemap %s: %s", canonical_map, exc)
            continue
        for raw in _sitemap_locs(body):
            absolute = canonicalize_url(raw)
            host = urlparse(absolute).netloc.lower()
            if not any(host == d or host.endswith(f".{d}") for d in allowed_domains):
                continue
            if is_blog_post_url(absolute):
                post_urls.add(absolute)
            elif is_category_url(absolute) and not is_pagination_url(absolute):
                category_urls.add(listing_base_url(absolute))

    logger.info(
        "Sitemap discovery %s -> posts=%d categories=%d",
        sitemap_index_url,
        len(post_urls),
        len(category_urls),
    )
    return {"post_urls": post_urls, "category_urls": category_urls}


# --- Step 1 (helper): parse listing page links ---


def extract_links_from_listing(html: str, page_url: str, allowed_domains: list[str]) -> dict[str, set[str]]:
    soup = BeautifulSoup(html, "lxml")
    post_urls: set[str] = set()
    pagination_urls: set[str] = set()
    category_urls: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = canonicalize_url(urljoin(page_url, href))
        host = urlparse(absolute).netloc.lower()
        if not any(host == d or host.endswith(f".{d}") for d in allowed_domains):
            continue

        if is_blog_post_url(absolute):
            post_urls.add(absolute)
        elif is_category_url(absolute):
            category_urls.add(absolute)
        elif _is_listing_pagination_link(absolute, page_url):
            pagination_urls.add(absolute)

    inferred = _infer_pagination_urls(soup, page_url, allowed_domains)
    pagination_urls.update(inferred)

    return {
        "post_urls": post_urls,
        "pagination_urls": pagination_urls,
        "category_urls": category_urls,
    }


def _is_listing_pagination_link(url: str, page_url: str) -> bool:
    path = urlparse(url).path
    if not re.search(r"/page/\d+/?$", path):
        return False
    base_page = listing_base_url(page_url)
    base_url = listing_base_url(url)
    if base_page == base_url:
        return True
    if urlparse(url).path.rstrip("/") == "/blog/page/" + path.split("/page/")[-1].strip("/"):
        return True
    return "/blog/" in path


def _infer_pagination_urls(soup: BeautifulSoup, page_url: str, allowed_domains: list[str]) -> set[str]:
    urls: set[str] = set()
    base = listing_base_url(page_url)
    text = soup.get_text(" ", strip=True)
    match = re.search(r"\b1\s+2\s+3.*?(\d+)\s*>", text)
    if not match:
        match = re.search(r"page\s*/\s*(\d+)", text, re.I)
    if not match:
        return urls

    max_page = int(match.group(1))
    parsed = urlparse(base)
    for n in range(2, max_page + 1):
        if parsed.path.rstrip("/") == "/blog":
            path = f"/blog/page/{n}/"
        else:
            path = parsed.path.rstrip("/") + f"/page/{n}/"
        candidate = canonicalize_url(urljoin(base, path))
        host = urlparse(candidate).netloc.lower()
        if any(host == d or host.endswith(f".{d}") for d in allowed_domains):
            urls.add(candidate)
    return urls


class ListingCrawler:
    """BFS over listing/category/pagination pages; registers post URLs in SQLite."""

    def __init__(
        self,
        fetcher: Fetcher,
        state_db: StateDB,
        artifacts: ArtifactStore | None = None,
        allowed_domains: list[str] | None = None,
        max_pages_per_listing: int = 50,
    ) -> None:
        self.fetcher = fetcher
        self.state_db = state_db
        self.artifacts = artifacts
        self.allowed_domains = allowed_domains or ["arnifi.com"]
        self.max_pages_per_listing = max_pages_per_listing

    def crawl(self, seed_listing_urls: list[str]) -> dict[str, set[str]]:
        listing_queue: deque[str] = deque()
        seen_listings: set[str] = set()
        discovered_posts: set[str] = set()
        discovered_categories: set[str] = set()

        for seed in seed_listing_urls:
            canonical = canonicalize_url(seed)
            listing_queue.append(canonical)
            self.state_db.upsert_listing_url(canonical)

        pages_crawled_per_base: dict[str, int] = {}

        while listing_queue:
            listing_url = listing_queue.popleft()
            if listing_url in seen_listings:
                continue
            seen_listings.add(listing_url)

            base = listing_base_url(listing_url)
            pages_crawled_per_base.setdefault(base, 0)
            if pages_crawled_per_base[base] >= self.max_pages_per_listing:
                logger.info("Max pages reached for listing base %s", base)
                continue

            try:
                result = self.fetcher.fetch(listing_url)
            except Exception as exc:
                logger.error("Failed to fetch listing %s: %s", listing_url, exc)
                continue

            html = result["html"]
            if self.artifacts:
                self.artifacts.save_html("listing", listing_url, html)

            links = extract_links_from_listing(html, listing_url, self.allowed_domains)

            for post_url in links["post_urls"]:
                if self.state_db.upsert_post_url(post_url, listing_url=listing_url):
                    discovered_posts.add(post_url)

            for category_url in links["category_urls"]:
                if category_url not in seen_listings:
                    discovered_categories.add(category_url)
                    self.state_db.upsert_listing_url(category_url)
                    listing_queue.append(category_url)

            for page_url in links["pagination_urls"]:
                if page_url not in seen_listings:
                    self.state_db.upsert_listing_url(page_url)
                    listing_queue.append(page_url)

            self.state_db.mark_listing_crawled(listing_url)
            pages_crawled_per_base[base] += 1
            logger.info(
                "Listing %s -> posts=%d pagination=%d categories=%d",
                listing_url,
                len(links["post_urls"]),
                len(links["pagination_urls"]),
                len(links["category_urls"]),
            )

        return {
            "post_urls": discovered_posts,
            "category_urls": discovered_categories,
            "listing_urls": seen_listings,
        }


# --- Step 3: Parse HTML into structured documents ---

NOISE_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"get in touch",
        r"join our newsletter",
        r"related articles",
        r"top uae packages",
        r"book a consultation",
        r"summarize this article",
        r"chatgpt",
        r"perplexity",
        r"load more",
        r"more articles by themes",
    ]
]

REMOVE_SELECTORS = [
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    ".newsletter",
    ".related",
    ".share",
    ".social",
    ".comments",
    ".comment",
    ".breadcrumb",
    ".breadcrumbs",
]


def extract_document(html: str, source_url: str) -> Document:
    soup = BeautifulSoup(html, "lxml")
    source_url = canonicalize_url(source_url)

    title = _extract_title(soup)
    author, published_at = _extract_meta(soup)
    category_name, category_url = _extract_category(soup, source_url)

    article = _find_article_root(soup)
    if article is None:
        article = soup.body or soup

    for selector in REMOVE_SELECTORS:
        for node in article.select(selector):
            node.decompose()

    blocks = _walk_blocks(article)
    sections = _pair_headings_to_sections(blocks, source_url)

    return Document(
        title=title,
        author=author,
        published_at=published_at,
        source_url=source_url,
        source_domain="arnifi.com",
        category_name=category_name,
        category_url=category_url,
        sections=sections,
    )


def _extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return clean_text(h1.get_text(" ", strip=True))
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return clean_text(og["content"])
    if soup.title:
        return clean_text(soup.title.get_text(strip=True))
    return "Untitled"


def _extract_meta(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    author = None
    published_at = None

    author_tag = soup.find("meta", attrs={"name": "author"})
    if author_tag and author_tag.get("content"):
        author = clean_text(author_tag["content"])

    for prop in ("article:published_time", "og:published_time"):
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            published_at = tag["content"]
            break

    if not author:
        byline = soup.find(string=re.compile(r"\bby\b", re.I))
        if byline and byline.parent:
            text = clean_text(byline.parent.get_text(" ", strip=True))
            m = re.search(r"by\s+(.+?)(?:\s+Jan|\s+Feb|\s+Mar|\s+Apr|\s+May|\s+Jun|\s+Jul|\s+Aug|\s+Sep|\s+Oct|\s+Nov|\s+Dec|\d{4}|$)", text, re.I)
            if m:
                author = clean_text(m.group(1))

    if not published_at:
        date_match = soup.find(string=re.compile(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}", re.I
        ))
        if date_match:
            published_at = clean_text(str(date_match))

    return author, published_at


def _extract_category(soup: BeautifulSoup, source_url: str) -> tuple[str | None, str | None]:
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/blog/category/" in href:
            return clean_text(anchor.get_text(" ", strip=True)), canonicalize_url(urljoin(source_url, href))
    return None, None


def _find_article_root(soup: BeautifulSoup) -> Tag | None:
    selectors = [
        "article",
        "main article",
        "main .entry-content",
        ".entry-content",
        "main",
        ".post-content",
        ".blog-content",
        "#content",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) > 200:
            return node
    return soup.find("article") or soup.find("main")


BYLINE_HEADING = re.compile(
    r"^\s*by\s+.+\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b.+\d{4}.*(?:min\s+read)?\s*$",
    re.I,
)
TOC_HEADING = re.compile(r"table of contents", re.I)
BREADCRUMB_HEADING = re.compile(r"^blogs?\b", re.I)


def _is_noise_heading(text: str) -> bool:
    return bool(
        BYLINE_HEADING.search(text)
        or TOC_HEADING.search(text)
        or BREADCRUMB_HEADING.search(text)
    )


def _is_toc_list(text: str) -> bool:
    lines = [line.strip(" -") for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    toc_keywords = ("introduction", "conclusion", "faq", "faqs", "step-by-step", "guide")
    hits = sum(1 for line in lines if any(k in line.lower() for k in toc_keywords))
    return hits >= max(2, len(lines) // 2)


def _is_noise_text(text: str) -> bool:
    lowered = text.lower().strip()
    if len(lowered) < 20:
        return True
    return any(pattern.search(lowered) for pattern in NOISE_PATTERNS)


def _table_to_text(table: Tag) -> str:
    rows: list[str] = []
    for tr in table.find_all("tr"):
        cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _walk_blocks(root: Tag) -> list[Block]:
    blocks: list[Block] = []
    block_index = 0
    skip_until_next_h2 = False

    def add_block(kind: str, text: str) -> None:
        nonlocal block_index
        text = clean_text(text)
        if not text or _is_noise_text(text):
            return
        if skip_until_next_h2 and kind != "h2":
            return
        if kind == "list" and _is_toc_list(text):
            return
        blocks.append(Block(index=block_index, kind=kind, text=text))
        block_index += 1

    for element in root.find_all(["h2", "h3", "h4", "p", "ul", "ol", "table"]):
        if element.name != "table" and element.find_parent("table"):
            continue
        if element.name == "p" and element.find_parent(["li", "ul", "ol"]):
            continue
        if element.name in {"ul", "ol"} and element.find_parent(["ul", "ol"]):
            continue

        if element.name in {"h2", "h3", "h4"}:
            heading_text = clean_text(element.get_text(" ", strip=True))
            if _is_noise_heading(heading_text):
                skip_until_next_h2 = bool(TOC_HEADING.search(heading_text))
                continue
            skip_until_next_h2 = False
            add_block(element.name, heading_text)
        elif element.name == "p":
            add_block("p", element.get_text(" ", strip=True))
        elif element.name in {"ul", "ol"}:
            items = [clean_text(li.get_text(" ", strip=True)) for li in element.find_all("li", recursive=False)]
            items = [item for item in items if item]
            if items:
                add_block("list", "\n".join(f"- {item}" for item in items))
        elif element.name == "table":
            text = _table_to_text(element)
            if text:
                add_block("table", text)

    deduped: list[Block] = []
    seen_text: set[str] = set()
    for block in blocks:
        key = f"{block.kind}:{block.text}"
        if key in seen_text:
            continue
        seen_text.add(key)
        deduped.append(block)
    return deduped


def _pair_headings_to_sections(blocks: list[Block], source_url: str) -> list[Section]:
    current_h2: str | None = None
    current_h3: str | None = None
    current_h4: str | None = None

    section_map: dict[str, dict[str, Any]] = {}

    def heading_path() -> list[str]:
        path = []
        if current_h2:
            path.append(current_h2)
        if current_h3:
            path.append(current_h3)
        if current_h4:
            path.append(current_h4)
        if not path:
            path = ["Introduction"]
        return path

    def section_key(path: list[str]) -> str:
        joined = " > ".join(path)
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]

    for block in blocks:
        if block.kind == "h2":
            current_h2 = block.text
            current_h3 = None
            current_h4 = None
            continue
        if block.kind == "h3":
            current_h3 = block.text
            current_h4 = None
            continue
        if block.kind == "h4":
            current_h4 = block.text
            continue

        path = heading_path()
        key = section_key(path)
        if key not in section_map:
            section_map[key] = {
                "heading_path": path,
                "text_parts": [],
                "block_spans": [],
            }
        section_map[key]["text_parts"].append(block.text)
        section_map[key]["block_spans"].append(block.index)

    sections: list[Section] = []
    for key, data in section_map.items():
        text = "\n\n".join(data["text_parts"]).strip()
        if not text:
            continue
        path: list[str] = data["heading_path"]
        if _is_noise_heading(path[-1]):
            continue
        sections.append(
            Section(
                section_id=f"{hashlib.sha1(source_url.encode()).hexdigest()[:12]}_{key}",
                heading_path=path,
                heading_text=path[-1],
                text=text,
                block_spans=data["block_spans"],
            )
        )
    return sections


# --- Step 4: Chunk documents for retrieval ---


def _get_encoder(model: str = "cl100k_base"):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    return len(_get_encoder(model).encode(text))


def chunk_document(
    document: Document,
    *,
    max_tokens: int = 400,
    overlap_tokens: int = 80,
    embedding_model: str = "text-embedding-3-small",
    listing_url: str | None = None,
    source_type: str = "blog",
    page_kind: str | None = None,
    product_type: str | None = None,
    discovered_via: str | None = None,
    content_type: str | None = None,
    publish_date: str | None = None,
    source_publication: str | None = None,
    event_date: str | None = None,
    event_time: str | None = None,
    location: str | None = None,
    partners: str | None = None,
    jurisdiction: str | None = None,
    industry: str | None = None,
    read_time: str | None = None,
) -> list[Chunk]:
    # tiktoken counts tokens so chunk size aligns with embedding model context limits.
    encoder = _get_encoder()
    crawl_ts = datetime.now(timezone.utc).isoformat()
    chunks: list[Chunk] = []

    for section in document.sections:
        section_chunks = _chunk_section_text(
            section.text,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            encoder=encoder,
        )
        for idx, chunk_text in enumerate(section_chunks):
            heading_path_str = " > ".join(section.heading_path)
            kind_line = f"PageKind: {page_kind}\n" if page_kind else ""
            type_line = f"ContentType: {content_type}\n" if content_type else ""
            disclaimer = ""
            if source_type == "website" and page_kind in WEBSITE_PRICE_PAGE_KINDS:
                disclaimer = (
                    "PriceDisclaimer: public starting-from price, not a quote. "
                    "Drive SKU rows win on exact fees.\n"
                )
            embed_text = (
                f"Title: {document.title}\n"
                f"{kind_line}"
                f"{type_line}"
                f"{disclaimer}"
                f"Section: {heading_path_str}\n"
                f"{chunk_text}"
            )
            # Prefer explicit hierarchy when Document is a service package.
            catalog_service = document.category_name
            catalog_package = document.title
            catalog_section = section.heading_path[0] if section.heading_path else None
            if page_kind == "product_detail" and catalog_service:
                embed_text = (
                    f"Service: {catalog_service}\n"
                    f"Package: {catalog_package}\n"
                    f"{kind_line}"
                    f"{disclaimer}"
                    f"Section: {heading_path_str}\n"
                    f"{chunk_text}"
                )
            chunk_id = hashlib.sha1(
                f"{document.source_url}|{section.section_id}|{idx}|{embedding_model}".encode()
            ).hexdigest()
            content_sha1 = hashlib.sha1(chunk_text.encode("utf-8")).hexdigest()

            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    source_url=document.source_url,
                    source_domain=document.source_domain,
                    doc_title=document.title,
                    doc_author=document.author,
                    doc_published_at=document.published_at,
                    doc_category=document.category_name,
                    doc_category_url=document.category_url,
                    listing_url=listing_url or document.category_url,
                    section_id=section.section_id,
                    heading_path=heading_path_str,
                    heading_text=section.heading_text,
                    chunk_index=idx,
                    chunk_text=chunk_text,
                    embed_text=embed_text,
                    chunk_char_len=len(chunk_text),
                    crawl_ts=crawl_ts,
                    content_sha1=content_sha1,
                    source_type=source_type,
                    page_kind=page_kind,
                    catalog_service=catalog_service if page_kind == "product_detail" else None,
                    catalog_package=catalog_package if page_kind == "product_detail" else None,
                    catalog_section=catalog_section if page_kind == "product_detail" else None,
                    product_type=product_type,
                    discovered_via=discovered_via,
                    content_type=content_type,
                    publish_date=publish_date,
                    source_publication=source_publication,
                    event_date=event_date,
                    event_time=event_time,
                    location=location,
                    partners=partners,
                    jurisdiction=jurisdiction,
                    industry=industry,
                    read_time=read_time,
                )
            )
    return chunks


def _chunk_section_text(
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    encoder,
) -> list[str]:
    tokens = encoder.encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        piece = encoder.decode(tokens[start:end]).strip()
        if piece:
            chunks.append(piece)
        if end >= len(tokens):
            break
        start = max(end - overlap_tokens, start + 1)
    return chunks


# --- Orchestration: steps 1–6 ---


class Indexer:
    """Build the vector index from Arnifi blog pages."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def crawl_listings(self, max_pages_per_listing: int | None = None) -> dict[str, Any]:
        crawler = ListingCrawler(
            fetcher=self.settings.fetcher,
            state_db=self.settings.state_db,
            artifacts=self.settings.artifacts,
            allowed_domains=self.settings.setting("crawl", "allowed_domains", default=[]),
            max_pages_per_listing=max_pages_per_listing
            or self.settings.setting("crawl", "max_pages_per_listing", default=50),
        )
        seeds = list(self.settings.setting("seeds", "listing_pages", default=[]) or [])
        allowed = self.settings.setting("crawl", "allowed_domains", default=["arnifi.com"])

        sitemap_new_posts = 0
        sitemap_new_cats = 0
        sitemap_index = self.settings.setting("seeds", "blog_sitemap_index", default=None)
        if sitemap_index:
            discovered = discover_blog_urls_from_sitemap(
                self.settings.fetcher,
                str(sitemap_index),
                allowed_domains=list(allowed),
            )
            for post_url in discovered["post_urls"]:
                if self.settings.state_db.upsert_post_url(post_url, listing_url=str(sitemap_index)):
                    sitemap_new_posts += 1
            for category_url in discovered["category_urls"]:
                if category_url not in seeds:
                    seeds.append(category_url)
                if self.settings.state_db.upsert_listing_url(category_url):
                    sitemap_new_cats += 1

        result = crawler.crawl(seeds)
        return {
            "sitemap": {
                "index": sitemap_index,
                "new_post_urls": sitemap_new_posts,
                "new_category_urls": sitemap_new_cats,
            },
            "discovery": {key: len(urls) for key, urls in result.items()},
            "state": self.settings.state_db.stats(),
        }

    def index_posts(
        self,
        limit: int | None = None,
        skip_unchanged: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        post_urls = self.settings.state_db.get_all_post_urls()
        # "Only new posts": never-ingested rows have no content_sha1 yet.
        if skip_unchanged:
            post_urls = [
                url
                for url in post_urls
                if not self.settings.state_db.get_post_content_sha1(url)
            ]
        if limit:
            post_urls = post_urls[:limit]

        flush_every = int(
            self.settings.setting("crawl", "ingest_flush_every_posts", default=20) or 20
        )
        pending_chunks: list[Chunk] = []
        processed = skipped = failed = 0
        upserted = 0

        def flush() -> None:
            nonlocal upserted, pending_chunks
            if not pending_chunks or dry_run:
                pending_chunks = []
                return
            upserted += self._embed_and_upsert(pending_chunks)
            pending_chunks = []

        for post_url in post_urls:
            status, chunks = self._index_one_post(post_url, skip_unchanged=False)
            if status == "processed" and chunks:
                pending_chunks.extend(chunks)
                processed += 1
                if processed % flush_every == 0:
                    flush()
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1

        flush()

        result: dict[str, Any] = {
            "posts_processed": processed,
            "posts_skipped": skipped,
            "posts_failed": failed,
            "posts_queued_new": len(post_urls),
            "chunks_upserted": upserted,
            "dry_run": dry_run,
        }
        if not dry_run and upserted:
            try:
                result["index_stats"] = self.settings.vectorstore.describe_stats()
            except Exception as exc:
                result["index_stats_error"] = str(exc)
        return result

    def run_full_index(
        self,
        post_limit: int | None = None,
        skip_unchanged: bool = True,
    ) -> dict[str, Any]:
        return {
            "crawl": self.crawl_listings(),
            "ingest": self.index_posts(
                limit=post_limit,
                skip_unchanged=skip_unchanged,
            ),
        }

    def extract_posts(self, limit: int = 3, url: str | None = None) -> dict[str, Any]:
        """Debug helper: fetch and save parsed JSON without embedding."""
        post_urls = [url] if url else self.settings.state_db.get_all_post_urls()[:limit]

        extracted = []
        failed = 0
        for post_url in post_urls:
            try:
                html = self._fetch_html(post_url)
                document = extract_document(html, post_url)
                path = self.settings.artifacts.save_extracted(post_url, document.model_dump())
                extracted.append(
                    {
                        "url": post_url,
                        "title": document.title,
                        "sections": len(document.sections),
                        "artifact": str(path),
                    }
                )
            except Exception as exc:
                failed += 1
                logger.error("Extract failed %s: %s", post_url, exc)
        return {"extracted": extracted, "failed": failed}

    def _index_one_post(
        self,
        post_url: str,
        *,
        skip_unchanged: bool,
    ) -> tuple[PostStatus, list[Chunk] | None]:
        try:
            html = self._fetch_html(post_url)
            document = extract_document(html, post_url)
            doc_sha1 = sha1_text(document.model_dump_json())

            if skip_unchanged and should_skip_post(
                self.settings.state_db.get_post_content_sha1(post_url),
                doc_sha1,
            ):
                logger.info("Skipping unchanged post %s", post_url)
                return "skipped", None

            self.settings.artifacts.save_extracted(post_url, document.model_dump())
            chunks = chunk_document(
                document,
                max_tokens=self.settings.setting("chunk", "max_tokens", default=400),
                overlap_tokens=self.settings.setting("chunk", "overlap_tokens", default=80),
                embedding_model=self.settings.setting(
                    "embedding", "model", default="amazon.titan-embed-text-v2:0"
                ),
            )
            self.settings.state_db.mark_post_crawled(post_url, doc_sha1)
            return "processed", chunks
        except Exception as exc:
            logger.error("Failed post ingest %s: %s", post_url, exc)
            return "failed", None

    def _fetch_html(self, post_url: str) -> str:
        result = self.settings.fetcher.fetch(post_url)
        html = result["html"]
        self.settings.artifacts.save_html("post", post_url, html)
        return html

    def _embed_and_upsert(self, chunks: list[Chunk]) -> int:
        vectors = self.settings.embedder.embed_texts([chunk.embed_text for chunk in chunks])
        return self.settings.vectorstore.upsert_chunks(chunks, vectors)
