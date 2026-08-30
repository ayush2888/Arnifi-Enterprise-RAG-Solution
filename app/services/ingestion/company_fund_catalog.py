"""Company (licence) + Fund catalog listing/ingest helpers (prodapi-first)."""

from __future__ import annotations

import re
from typing import Any

import requests

from app.services.prodapi.client import (
    ProdapiClient,
    micro_service_page_url,
    setup_product_page_url,
)
from app.utils.helpers import canonicalize_url, get_logger

logger = get_logger(__name__)

LICENCE_LISTING_URL = "https://arnifi.com/product-listing?productType=licence"
FUNDS_LISTING_URL = "https://arnifi.com/services/funds"
GUIDES_LISTING_URL = "https://arnifi.com/business-guides"

DISCOVERED_VIA_LICENCE = "product-listing"
DISCOVERED_VIA_FUNDS = "funds-catalog"
DISCOVERED_VIA_GUIDES = "business-guides"

# Prodapi micro-service type for /business-guides packages.
GUIDES_SERVICE_TYPE = "Product Insights"


def fetch_live_stated_total(listing_url: str, *, timeout: int = 40) -> int | None:
    """
    Read the marketing page's stated package count from HTML (not full card scrape).

    Licence: \"Showing 9 out of 117 packages\"
    Funds: \"Result - 41 Packages\"
    """
    try:
        html = requests.get(
            listing_url,
            timeout=timeout,
            headers={
                "Accept": "text/html",
                "User-Agent": "ArnifiKnowledgeBot/1.0",
            },
        ).text
    except Exception as exc:
        logger.warning("Failed fetching live total from %s: %s", listing_url, exc)
        return None
    plain = re.sub(r"<[^>]+>", " ", html)
    plain = re.sub(r"\s+", " ", plain)
    m = re.search(
        r"Showing\s+\d+\s+out of\s+(\d+)\s+packages",
        plain,
        flags=re.IGNORECASE,
    )
    if m:
        return int(m.group(1))
    m = re.search(
        r"Result\s*[-ΓÇô]?\s*(\d+)\s+Packages?",
        plain,
        flags=re.IGNORECASE,
    )
    if m:
        return int(m.group(1))
    return None


def _attrs(row: dict[str, Any]) -> dict[str, Any]:
    attrs = row.get("attributes")
    if isinstance(attrs, dict):
        return attrs
    return row


def _country_from_licence_row(attrs: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (country_name, country_slug) from Strapi country relation if present."""
    country = attrs.get("country")
    if not isinstance(country, dict):
        return None, None
    data = country.get("data")
    if isinstance(data, dict):
        cattrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else data
        if isinstance(cattrs, dict):
            name = cattrs.get("name") or cattrs.get("countryName") or cattrs.get("title")
            slug = cattrs.get("slug")
            return (
                str(name).strip() if name else None,
                str(slug).strip() if slug else None,
            )
    return None, None


def _format_price(amount: Any, currency: Any) -> str | None:
    if amount is None or amount == "":
        return None
    try:
        num = float(amount)
        if num.is_integer():
            amount_s = str(int(num))
        else:
            amount_s = f"{num:.2f}"
    except (TypeError, ValueError):
        amount_s = str(amount).strip()
    cur = str(currency or "").strip().upper()
    if cur:
        return f"{cur} {amount_s}"
    return amount_s


def _tags_from_row(attrs: dict[str, Any]) -> list[str]:
    """Only explicit per-card badges ΓÇö never invent from filter panels."""
    tags: list[str] = []
    rank = attrs.get("bestSellerRank")
    try:
        if rank is not None and int(rank) > 0:
            tags.append("Best Seller")
    except (TypeError, ValueError):
        pass
    # badgeLabel on Funds is jurisdiction (e.g. UAE), not a promo tag ΓÇö skip.
    return tags


def map_licence_listing_row(row: dict[str, Any]) -> dict[str, Any]:
    attrs = _attrs(row)
    pid = row.get("id")
    slug = str(attrs.get("slug") or "").strip()
    country_name, country_slug = _country_from_licence_row(attrs)
    detail_url = canonicalize_url(
        setup_product_page_url(
            slug or "package",
            product_id=pid,
            country_slug=country_slug or "business-setup",
        )
    )
    price = _format_price(
        attrs.get("startingArnifiPrice") or attrs.get("startingOfficialPrice"),
        attrs.get("currency"),
    )
    banner = attrs.get("productBanner")
    banner_heading = (
        banner.get("heading") if isinstance(banner, dict) else None
    )
    name = attrs.get("productName") or banner_heading or slug or f"licence-{pid}"
    return {
        "product_type": "licence",
        "package_name": str(name).strip(),
        "jurisdiction": country_name,
        "country": country_name,
        "estimated_time": None,  # not on licence list cards; detail may have steps
        "starting_price": price,
        "detail_url": detail_url,
        "tags": _tags_from_row(attrs),
        "product_id": pid,
        "slug": slug,
        "discovered_via": DISCOVERED_VIA_LICENCE,
    }


def map_fund_listing_row(row: dict[str, Any]) -> dict[str, Any]:
    slug = str(row.get("slug") or "").strip()
    sid = row.get("id")
    detail_url = canonicalize_url(
        micro_service_page_url("Funds", slug or "package", service_id=sid)
    )
    # badgeLabel is jurisdiction chip on fund cards (UAE / Guernsey / ΓÇª)
    jurisdiction = str(row.get("badgeLabel") or "").strip() or None
    price = _format_price(
        row.get("startingPrice") or row.get("currentPrice"), row.get("currency")
    )
    name = (
        str(row.get("bottomTitle") or "").strip()
        or str(row.get("shortDescription") or "").strip()
        or slug.replace("-", " ").strip()
        or f"fund-{sid}"
    )
    return {
        "product_type": "fund",
        "package_name": name,
        "jurisdiction": jurisdiction,
        "country": jurisdiction,
        "estimated_time": (
            str(row.get("estimatedDeliveryTime")).strip()
            if row.get("estimatedDeliveryTime")
            else None
        ),
        "starting_price": price,
        "detail_url": detail_url,
        "tags": _tags_from_row(row),
        "product_id": sid,
        "slug": slug,
        "discovered_via": DISCOVERED_VIA_FUNDS,
    }


def discover_licence_catalog(
    client: ProdapiClient | None = None,
    *,
    country_id: int | str | None = None,
) -> dict[str, Any]:
    client = client or ProdapiClient()
    live_total = fetch_live_stated_total(LICENCE_LISTING_URL)
    rows = client.list_product_pages(
        product_type="licence",
        country_id=country_id,
        populate_country=True,
    )
    cards = [map_licence_listing_row(r) for r in rows]
    urls = [c["detail_url"] for c in cards]
    return {
        "product_type": "licence",
        "listing_url": LICENCE_LISTING_URL,
        "discovery": "prodapi",
        "live_stated_total": live_total,
        "discovered": len(cards),
        "complete": live_total is not None and len(cards) == live_total,
        "items": cards,
        "detail_urls": urls,
    }


def discover_funds_catalog(client: ProdapiClient | None = None) -> dict[str, Any]:
    client = client or ProdapiClient()
    live_total = fetch_live_stated_total(FUNDS_LISTING_URL)
    rows = client.list_micro_services("Funds")
    cards = [map_fund_listing_row(r) for r in rows]
    urls = [c["detail_url"] for c in cards]
    return {
        "product_type": "fund",
        "listing_url": FUNDS_LISTING_URL,
        "discovery": "prodapi",
        "live_stated_total": live_total,
        "discovered": len(cards),
        "complete": live_total is not None and len(cards) == live_total,
        "items": cards,
        "detail_urls": urls,
    }


def map_guide_listing_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map Product Insights micro-service row ΓåÆ Business Guide catalog card."""
    slug = str(row.get("slug") or "").strip()
    sid = row.get("id")
    detail_url = canonicalize_url(
        micro_service_page_url(
            GUIDES_SERVICE_TYPE, slug or "package", service_id=sid
        )
    )
    price = _format_price(
        row.get("startingPrice") or row.get("currentPrice"), row.get("currency")
    )
    name = (
        str(row.get("bottomTitle") or "").strip()
        or str(row.get("title") or "").strip()
        or str(row.get("shortDescription") or "").strip()
        or slug.replace("-", " ").strip()
        or f"guide-{sid}"
    )
    return {
        "product_type": "guide",
        "package_name": name,
        "jurisdiction": (
            str(row.get("badgeLabel") or "").strip() or None
        ),
        "country": str(row.get("badgeLabel") or "").strip() or None,
        "estimated_time": (
            str(row.get("estimatedDeliveryTime")).strip()
            if row.get("estimatedDeliveryTime")
            else None
        ),
        "starting_price": price,
        "detail_url": detail_url,
        "tags": _tags_from_row(row),
        "product_id": sid,
        "slug": slug,
        "discovered_via": DISCOVERED_VIA_GUIDES,
        "service_type": GUIDES_SERVICE_TYPE,
    }


def discover_guides_catalog(client: ProdapiClient | None = None) -> dict[str, Any]:
    """Business Guides via prodapi Product Insights + live Result - N Packages gate."""
    client = client or ProdapiClient()
    live_total = fetch_live_stated_total(GUIDES_LISTING_URL)
    rows = client.list_micro_services(GUIDES_SERVICE_TYPE)
    cards = [map_guide_listing_row(r) for r in rows]
    urls = [c["detail_url"] for c in cards]
    return {
        "product_type": "guide",
        "listing_url": GUIDES_LISTING_URL,
        "discovery": "prodapi",
        "live_stated_total": live_total,
        "discovered": len(cards),
        "complete": live_total is not None and len(cards) == live_total,
        "items": cards,
        "detail_urls": urls,
    }
