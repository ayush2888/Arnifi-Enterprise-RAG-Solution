"""URL helpers, text cleanup, and logging setup."""

from __future__ import annotations

import logging
import re
import sys
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


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


_PRESERVE_QUERY_PATHS = frozenset({"/product-listing", "/services/funds"})
_KEEP_QUERY_KEYS = frozenset({"countries", "producttype", "authority"})

_DENIED_PREFIXES = (
    "/checkout",
    "/cart",
    "/account",
    "/api/",
    "/admin",
    "/private",
    "/cost-calculator",
    "/arnifi-organogram",
    "/aml-screener",
    "/products/arnifi-docs",
    "/products/free-ai-assistance",
    "/products/affiliate-program",
    "/career",
    "/privacy-policy",
    "/terms-conditions",
)

# Short country overview paths after /country-overview/{slug} redirects.
COUNTRY_SHORT_PATHS = frozenset(
    {
        "/ae",
        "/sa",
        "/ksa",
        "/sg",
        "/uk",
        "/gb",
        "/hk",
        "/my",
        "/cy",
        "/ie",
        "/lu",
        "/mu",
        "/gg",
        "/vg",
        "/bvi",
        "/ky",
        "/pr",
        "/vc",
        "/svg",
    }
)

# Listing grids and the homepage are discovered, not embedded as RAG docs.
SKIP_INGEST_PAGE_KINDS = frozenset({"package_listing", "hub"})

WEBSITE_PRICE_PAGE_KINDS = frozenset(
    {
        "pricing",
        "service",
        "product",
        "jurisdiction",
        "fund",
        "service_landing",
        "service_package",
        "product_detail",
        "country_overview",
    }
)


def canonicalize_url(url: str, base: str | None = None) -> str:
    """Normalize URLs.

    Blog paths still drop the query string (same as the original crawler).
    product-listing and services/funds keep countries / productType / authority.
    """
    if base:
        url = urljoin(base, url)
    parsed = urlparse(url.strip())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    last = path.rstrip("/").rsplit("/", 1)[-1]
    # Keep /llms.txt as a file path; do not force a trailing slash.
    if "." in last and not last.startswith("."):
        path = path.rstrip("/") or "/"
    elif not path.endswith("/"):
        path = f"{path}/"

    query = ""
    path_key = path.rstrip("/") or "/"
    if path_key in _PRESERVE_QUERY_PATHS:
        kept = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if key.lower() in _KEEP_QUERY_KEYS
        ]
        if kept:
            query = urlencode(kept)
    return urlunparse((scheme, netloc, path, "", query, ""))


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


def _website_path(url: str) -> str:
    return urlparse(url).path.rstrip("/") or "/"


def is_denied_website_url(url: str, extra_prefixes: list[str] | None = None) -> bool:
    path = _website_path(url).lower()
    prefixes = list(_DENIED_PREFIXES)
    for prefix in extra_prefixes or []:
        prefixes.append(prefix.lower().rstrip("/") or prefix.lower())
    for prefix in prefixes:
        p = prefix if prefix.startswith("/") else f"/{prefix}"
        if path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") or path.startswith(p):
            return True
    if path.startswith("/blog"):
        return True
    return False


def is_homepage_url(url: str) -> bool:
    return _website_path(url) in {"/", ""}


def is_country_overview_url(url: str) -> bool:
    path = _website_path(url)
    if path in COUNTRY_SHORT_PATHS:
        return True
    return path.startswith("/country-overview/") and path.count("/") == 2


def is_service_landing_url(url: str) -> bool:
    path = _website_path(url)
    parts = [p for p in path.split("/") if p]
    if path.startswith("/services/overview/") and path.count("/") == 3:
        return True
    if path.startswith("/services/funds"):
        return False
    if path.startswith("/services/") and len(parts) == 2:
        return True
    return False


def is_service_package_url(url: str) -> bool:
    path = _website_path(url)
    parts = [p for p in path.split("/") if p]
    if len(parts) < 3 or parts[0] != "services":
        return False
    if parts[1] in {"overview", "funds"}:
        return False
    return True


def should_skip_website_ingest(url: str, page_kind: str | None = None) -> bool:
    """True for hubs/listings we discover but do not embed."""
    kind = page_kind or infer_page_kind(url)
    if kind in SKIP_INGEST_PAGE_KINDS:
        return True
    path = _website_path(url)
    if path in {"/business-guides"}:
        return True
    return False


def is_website_hub_url(url: str) -> bool:
    path = _website_path(url)
    if is_homepage_url(url):
        return True
    if path in ("/llms.txt", "/pricing-master-list", "/contact-us", "/case-studies"):
        return True
    if path in {"/announcement/press-releases", "/announcement/events"}:
        return True
    if path.startswith("/case-studies/industries/") or path.startswith(
        "/case-studies/jurisdiction/"
    ):
        return True
    if is_country_overview_url(url):
        return True
    if path.startswith("/product-listing"):
        return True
    if path.startswith("/services/funds"):
        return True
    if is_service_landing_url(url):
        return True
    return False


def is_case_study_url(url: str) -> bool:
    path = _website_path(url)
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2 or parts[0] != "case-studies":
        return False
    if parts[1] in {"industries", "jurisdiction"}:
        return False
    return True


def is_press_release_url(url: str) -> bool:
    path = _website_path(url)
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 3 and parts[0] == "announcement" and parts[1] == "press-releases"


def is_event_url(url: str) -> bool:
    path = _website_path(url)
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 3 and parts[0] == "announcement" and parts[1] == "events"


def is_product_detail_url(url: str) -> bool:
    """SKU/detail pages if the site exposes a crawlable path.

    Listing cards often use buttons (no href). This matches known detail shapes.
    """
    path = _website_path(url)
    parts = [p for p in path.split("/") if p]
    if not parts:
        return False
    if parts[0] in {"product-details", "product-detail"} and len(parts) >= 2:
        return True
    if parts[0] == "products" and len(parts) >= 2:
        return not is_denied_website_url(url)
    return False


def infer_page_kind(url: str) -> str:
    path = _website_path(url)
    if is_homepage_url(url):
        return "hub"
    if path.endswith("llms.txt") or path in {"/contact-us"}:
        return "company"
    if is_press_release_url(url):
        return "press_release"
    if is_event_url(url):
        return "event"
    if path in {"/announcement/press-releases", "/announcement/events"} or path.startswith(
        "/announcement/"
    ):
        return "hub"
    if path.startswith("/pricing-master-list"):
        return "pricing"
    if path in {"/business-guides"} or path.startswith("/business-guides/"):
        return "business_guide"
    if is_case_study_url(url):
        return "case_study"
    if path == "/case-studies" or path.startswith("/case-studies/industries/"):
        return "hub"
    if is_country_overview_url(url):
        return "country_overview"
    if path.startswith("/product-listing"):
        return "package_listing"
    if path.startswith("/services/funds"):
        return "fund"
    if is_product_detail_url(url):
        return "product_detail"
    if is_service_package_url(url):
        return "service_package"
    if is_service_landing_url(url) or path.startswith("/services/"):
        return "service_landing"
    return "service_landing"
