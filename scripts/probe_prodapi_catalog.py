"""
Step 1 ΓÇö Probe Arnifi public catalog APIs (no auth).

Run from the repo root:
  .\\venv\\Scripts\\python.exe scripts\\probe_prodapi_catalog.py

This does NOT write to Pinecone. It only prints JSON shapes so we know
what to chunk in a later ingest step.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import requests

BASE = "https://prodapi.arnifi.com/api"
HEADERS = {
    "Accept": "application/json",
    "Origin": "https://arnifi.com",
    "Referer": "https://arnifi.com/",
}
TIMEOUT = 40


def get_json(path: str) -> Any:
    """HTTP GET ΓåÆ parse JSON. path is relative to BASE (e.g. '/get-countries')."""
    url = f"{BASE}{path}"
    print(f"\n>>> GET {url}")
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def preview(label: str, value: Any, max_chars: int = 280) -> None:
    """Print a short preview so the terminal stays readable."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ").strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    print(f"  {label}: {text}")


def main() -> None:
    # --- Locations ---
    countries_payload = get_json("/get-countries")
    countries = countries_payload.get("data") or []
    print(f"Countries on navbar: {len(countries)}")
    names = [c.get("countryName") or c.get("slug") for c in countries]
    print(f"  names: {names}")

    # Pick Guernsey if present, else first country
    sample_country = next(
        (c for c in countries if str(c.get("slug", "")).lower() == "guernsey"),
        countries[0] if countries else None,
    )
    if not sample_country:
        raise SystemExit("No countries returned ΓÇö stop here and ping the API team.")

    country_slug = sample_country["slug"]  # e.g. "Guernsey" (capital G)
    overview = get_json(f"/country-overview/{quote(country_slug)}")
    country_data = overview.get("data") or overview
    print(f"Country overview keys ({country_slug}): {sorted(country_data.keys())}")
    preview("title", country_data.get("title"))
    preview("subHeader", country_data.get("subHeader"))
    preview("overviewDetail", country_data.get("overviewDetail"))
    preview("funds_count", len(country_data.get("funds") or []))
    preview("FAQs_count", len(country_data.get("FAQs") or []))
    preview("processStep_count", len(country_data.get("processStep") or []))

    # --- Services (Funds is one service type) ---
    types_payload = get_json("/micro-services/")
    service_types = [
        t.get("serviceType") for t in (types_payload.get("serviceTypes") or [])
    ]
    print(f"Service types ({len(service_types)}): {service_types}")

    funds_list = get_json("/micro-services/Funds")
    fund_items = funds_list.get("data") or []
    print(f"Funds packages listed: {len(fund_items)}")
    if fund_items:
        print("  first 5 slugs:")
        for item in fund_items[:5]:
            print(f"    - {item.get('slug')}  (id={item.get('id')})")

    fund_slug = "guernsey-authorised-closed-ended-collective-investment-schemes"
    # Prefer that Guernsey slug if present; otherwise first fund in the list
    if not any(i.get("slug") == fund_slug for i in fund_items) and fund_items:
        fund_slug = fund_items[0]["slug"]

    detail = get_json(f"/micro-services/Funds/{quote(fund_slug)}")
    fund = detail.get("data") or detail
    print(f"Fund detail keys ({fund_slug}): {sorted(fund.keys())}")
    preview("title", fund.get("title"))
    preview("price", fund.get("price") or fund.get("startingPrice"))
    preview("description", fund.get("description"))
    preview("processFlow_count", len(fund.get("processFlow") or []))
    preview("faqs_count", len(fund.get("faqs") or []))
    preview("documents_count", len(fund.get("documentsFromAuthority") or []))
    preview("highlights_count", len(fund.get("highlightsAndBenifits") or []))

    print("\nStep 1 OK ΓÇö APIs respond with structured catalog data.")
    print("Next (Step 2): turn these JSON fields into RAG chunks (no Pinecone yet).")


if __name__ == "__main__":
    main()
