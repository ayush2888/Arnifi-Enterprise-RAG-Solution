"""Public Arnifi prodapi client (locations + catalog). No auth required."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.utils.helpers import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://prodapi.arnifi.com/api"
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://arnifi.com",
    "Referer": "https://arnifi.com/",
}


class ProdapiClient:
    """Thin HTTP client for prodapi.arnifi.com public catalog endpoints."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = 40,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        logger.info("prodapi GET %s", url)
        response = self.session.get(url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def list_countries(self) -> list[dict[str, Any]]:
        """Navbar locations from GET /get-countries."""
        payload = self._get("/get-countries")
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise RuntimeError("get-countries did not return a data list")
        return data

    def get_country_overview(self, slug: str) -> dict[str, Any]:
        """Country page body from GET /country-overview/:slug."""
        encoded = quote(str(slug).strip(), safe="")
        payload = self._get(f"/country-overview/{encoded}")
        if isinstance(payload, dict) and "data" in payload:
            data = payload["data"]
            if not isinstance(data, dict):
                raise RuntimeError(f"country-overview/{slug} data is not an object")
            return data
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(f"Unexpected country-overview payload for {slug}")

    def list_service_types(self) -> list[str]:
        payload = self._get("/micro-services/")
        types = payload.get("serviceTypes") if isinstance(payload, dict) else None
        if not isinstance(types, list):
            raise RuntimeError("micro-services/ did not return serviceTypes")
        names: list[str] = []
        for row in types:
            if isinstance(row, dict) and row.get("serviceType"):
                names.append(str(row["serviceType"]))
        return names

    def list_micro_services(self, service_type: str) -> list[dict[str, Any]]:
        """List all packages for a type (follows prodapi page / pageCount pagination)."""
        encoded = quote(str(service_type).strip(), safe="")
        path = f"/micro-services/{encoded}"
        all_rows: list[dict[str, Any]] = []
        seen_slugs: set[str] = set()
        page = 1
        page_count = 1
        while page <= page_count:
            payload = self._get(path, params={"page": page})
            data = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(data, list):
                raise RuntimeError(
                    f"micro-services/{service_type} did not return a data list"
                )
            for row in data:
                if not isinstance(row, dict):
                    continue
                slug = str(row.get("slug") or "").strip()
                if slug and slug in seen_slugs:
                    continue
                if slug:
                    seen_slugs.add(slug)
                all_rows.append(row)
            meta = payload.get("meta") if isinstance(payload, dict) else None
            if isinstance(meta, dict):
                try:
                    page_count = max(1, int(meta.get("pageCount") or 1))
                except (TypeError, ValueError):
                    page_count = 1
            else:
                page_count = 1
            if not data:
                break
            page += 1
            # Safety cap against infinite loops if API misreports pageCount
            if page > 50:
                logger.warning(
                    "Stopping micro-services pagination for %s after 50 pages",
                    service_type,
                )
                break
        logger.info(
            "prodapi micro-services/%s: %d package(s) across %d page(s)",
            service_type,
            len(all_rows),
            page_count,
        )
        return all_rows

    def get_micro_service_detail(self, service_type: str, slug: str) -> dict[str, Any]:
        type_enc = quote(str(service_type).strip(), safe="")
        slug_enc = quote(str(slug).strip(), safe="")
        payload = self._get(f"/micro-services/{type_enc}/{slug_enc}")
        if isinstance(payload, dict) and "data" in payload:
            data = payload["data"]
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"micro-services/{service_type}/{slug} data is not an object"
                )
            return data
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(f"Unexpected micro-service payload for {service_type}/{slug}")

    def get_product_page_by_id(self, product_id: int | str) -> dict[str, Any] | None:
        """Setup/licence product attributes via Strapi filters (slug path often 500s)."""
        payload = self._get(
            "/product-pages",
            params={
                "filters[id][$eq]": str(product_id),
                "populate": "*",
            },
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            return None
        row = data[0]
        if isinstance(row, dict):
            return row
        return None

    def list_product_pages(
        self,
        *,
        product_type: str = "licence",
        country_id: int | str | None = None,
        page_size: int = 100,
        populate_country: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Paginated /product-pages rows filtered by productType.

        Global licence grid (product-listing?productType=licence) uses
        productType=licence without country (~117 live). Per-country grids
        pass country_id (UAE Γëê 78).
        """
        all_rows: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        page = 1
        page_count = 1
        while page <= page_count:
            params: dict[str, Any] = {
                "filters[productType][$eq]": str(product_type).strip(),
                "pagination[page]": page,
                "pagination[pageSize]": page_size,
            }
            if country_id is not None:
                params["filters[country][id][$eq]"] = str(country_id)
            if populate_country:
                params["populate[country]"] = "*"
            payload = self._get("/product-pages", params=params)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise RuntimeError(
                    f"product-pages productType={product_type} did not return a data list"
                )
            for row in data:
                if not isinstance(row, dict):
                    continue
                rid = row.get("id")
                try:
                    iid = int(rid) if rid is not None else None
                except (TypeError, ValueError):
                    iid = None
                if iid is not None:
                    if iid in seen_ids:
                        continue
                    seen_ids.add(iid)
                all_rows.append(row)
            meta = payload.get("meta") if isinstance(payload, dict) else None
            pagination = meta.get("pagination") if isinstance(meta, dict) else None
            if isinstance(pagination, dict):
                try:
                    page_count = max(1, int(pagination.get("pageCount") or 1))
                except (TypeError, ValueError):
                    page_count = 1
            else:
                page_count = 1
            if not data:
                break
            page += 1
            if page > 50:
                logger.warning(
                    "Stopping product-pages pagination for productType=%s after 50 pages",
                    product_type,
                )
                break
        logger.info(
            "prodapi product-pages productType=%s country_id=%s rows=%d",
            product_type,
            country_id,
            len(all_rows),
        )
        return all_rows

    def list_licence_packages_for_country(
        self,
        country_id: int | str,
        *,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Full setup/licence package list for a country (product-listing pages).

        Country overview `productPages` is only the short Top Packages strip
        (often 2). The website grid uses /product-pages filtered by country +
        productType=licence (UAE Γëê 78, Saudi Γëê 6).
        """
        return self.list_product_pages(
            product_type="licence",
            country_id=country_id,
            page_size=page_size,
            populate_country=True,
        )

    def list_case_studies(self) -> list[dict[str, Any]]:
        """List case study cards from GET /case-studies."""
        payload = self._get("/case-studies")
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise RuntimeError("case-studies did not return a data list")
        return [row for row in data if isinstance(row, dict)]

    def get_case_study(self, slug: str) -> dict[str, Any]:
        """Case study body from GET /case-studies/:slug."""
        encoded = quote(str(slug).strip(), safe="")
        payload = self._get(f"/case-studies/{encoded}")
        if isinstance(payload, dict) and "data" in payload:
            data = payload["data"]
            if not isinstance(data, dict):
                raise RuntimeError(f"case-studies/{slug} data is not an object")
            return data
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(f"Unexpected case-study payload for {slug}")


def country_page_url(shortcode: str | None, *, slug: str | None = None) -> str:
    """Canonical public country URL used as Pinecone source_url (e.g. /gg)."""
    code = (shortcode or "").strip().lower()
    if code:
        return f"https://arnifi.com/{code}"
    fallback = (slug or "unknown").strip().lower().replace(" ", "-")
    return f"https://arnifi.com/country-overview/{fallback}"


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "item"


def micro_service_page_url(
    service_type: str,
    slug: str,
    *,
    service_id: int | str | None = None,
) -> str:
    """Canonical arnifi.com URL for a micro-service package detail."""
    type_slug = _slugify(service_type)
    path = f"https://arnifi.com/product-details/services/{type_slug}/{slug}"
    if service_id is not None:
        path = f"{path}/{service_id}"
    return path


def setup_product_page_url(
    slug: str,
    *,
    product_id: int | str | None = None,
    country_slug: str | None = None,
) -> str:
    country = _slugify(country_slug or "business-setup")
    path = f"https://arnifi.com/product-details/business-setup/{country}/{slug}"
    if product_id is not None:
        path = f"{path}/{product_id}"
    return path
