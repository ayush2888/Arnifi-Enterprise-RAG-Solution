"""URL helpers, text cleanup, and logging setup."""

from __future__ import annotations

import logging
import re
import sys
import unicodedata
from urllib.parse import urljoin, urlparse, urlunparse


# --- Logging ---


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# --- Text ---


def normalize_whitespace(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_text(text: str) -> str:
    text = normalize_whitespace(text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


# --- URLs ---


def canonicalize_url(url: str, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    parsed = urlparse(url.strip())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = f"{path}/"
    return urlunparse((scheme, netloc, path, "", "", ""))


def is_allowed_url(url: str, allowed_domains: list[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def is_blog_post_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path.startswith("/blog/"):
        return False
    if path == "/blog":
        return False
    if "/blog/category/" in path:
        return False
    if "/blog/page/" in path:
        return False
    if path.endswith("/feed") or path.endswith("/feed/"):
        return False
    parts = [p for p in path.split("/") if p]
    return len(parts) == 2 and parts[0] == "blog"


def is_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path in ("/blog", ""):
        return True
    if "/blog/category/" in path:
        return True
    if re.search(r"/blog/(category/[^/]+/)?page/\d+", path + "/"):
        return True
    return False


def is_category_url(url: str) -> bool:
    return "/blog/category/" in urlparse(url).path


def is_pagination_url(url: str) -> bool:
    return bool(re.search(r"/page/\d+/?$", urlparse(url).path))


def listing_base_url(url: str) -> str:
    path = urlparse(url).path
    path = re.sub(r"/page/\d+/?$", "/", path)
    if not path.endswith("/"):
        path += "/"
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
