"""Probe full country package lists from prodapi (avoid PowerShell $ eating)."""

from __future__ import annotations

import json

import requests

from app.services.prodapi.client import ProdapiClient

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://arnifi.com",
    "Referer": "https://arnifi.com/",
}


def main() -> None:
    client = ProdapiClient()
    countries = client.list_countries()
    by_name = {str(r.get("countryName")): r for r in countries}

    for name in ("UAE", "Saudi Arabia", "Singapore", "Luxembourg", "Guernsey"):
        row = by_name.get(name)
        if not row:
            print("missing", name)
            continue
        cid = row["id"]
        # Full licence listing used by product-listing pages
        params = {
            "filters[country][id][$eq]": str(cid),
            "pagination[pageSize]": 100,
            "pagination[page]": 1,
        }
        r = requests.get(
            "https://prodapi.arnifi.com/api/product-pages",
            params=params,
            headers=HEADERS,
            timeout=40,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data") or []
        meta = payload.get("meta") or {}
        print(f"{name} id={cid} product-pages total meta={meta} len={len(data)}")

        # Also try productType licence only
        params2 = {
            "filters[country][id][$eq]": str(cid),
            "filters[productType][$eq]": "licence",
            "pagination[pageSize]": 100,
            "pagination[page]": 1,
        }
        r2 = requests.get(
            "https://prodapi.arnifi.com/api/product-pages",
            params=params2,
            headers=HEADERS,
            timeout=40,
        )
        if r2.ok:
            j2 = r2.json()
            print(
                f"  +productType=licence meta={j2.get('meta')} len={len(j2.get('data') or [])}"
            )

        overview = client.get_country_overview(str(row.get("slug")))
        print(
            f"  country-overview top packages={len(overview.get('productPages') or [])} "
            f"funds={len(overview.get('funds') or [])}"
        )


if __name__ == "__main__":
    main()
