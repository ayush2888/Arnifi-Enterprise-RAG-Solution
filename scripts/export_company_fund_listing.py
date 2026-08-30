#!/usr/bin/env python
"""Export Company/Licence + Fund catalog listing JSON from prodapi (Task 2).

Writes data/eval/company_fund_catalog_listing.json and prints completeness
vs live marketing page stated totals.

Usage:
  python scripts/export_company_fund_listing.py
  python scripts/export_company_fund_listing.py --licence-only
  python scripts/export_company_fund_listing.py --funds-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ingestion.company_fund_catalog import (
    discover_funds_catalog,
    discover_guides_catalog,
    discover_licence_catalog,
)
from app.services.prodapi.client import ProdapiClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--licence-only", action="store_true")
    parser.add_argument("--funds-only", action="store_true")
    parser.add_argument("--guides-only", action="store_true")
    parser.add_argument(
        "--country-id",
        default=None,
        help="Optional country id for licence listing (e.g. 1=UAE)",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "eval" / "company_fund_catalog_listing.json"),
    )
    args = parser.parse_args()

    exclusive = sum(
        bool(x) for x in (args.licence_only, args.funds_only, args.guides_only)
    )
    if exclusive > 1:
        print("Pass at most one of --licence-only / --funds-only / --guides-only", file=sys.stderr)
        return 2

    client = ProdapiClient()
    catalogs = []
    if args.guides_only:
        catalogs.append(discover_guides_catalog(client))
    elif args.licence_only:
        catalogs.append(discover_licence_catalog(client, country_id=args.country_id))
    elif args.funds_only:
        catalogs.append(discover_funds_catalog(client))
    else:
        catalogs.append(discover_licence_catalog(client, country_id=args.country_id))
        catalogs.append(discover_funds_catalog(client))
        catalogs.append(discover_guides_catalog(client))

    # Public listing cards only (strip internal ids for the Task 2 schema export)
    public_items = []
    for cat in catalogs:
        for item in cat.get("items") or []:
            public_items.append(
                {
                    "product_type": item.get("product_type"),
                    "package_name": item.get("package_name"),
                    "jurisdiction": item.get("jurisdiction"),
                    "country": item.get("country"),
                    "estimated_time": item.get("estimated_time"),
                    "starting_price": item.get("starting_price"),
                    "detail_url": item.get("detail_url"),
                    "tags": item.get("tags") or [],
                    "discovered_via": item.get("discovered_via"),
                }
            )

    report = {
        "discovery": "prodapi",
        "note": (
            "Listings come from prodapi (not DOM Load More). "
            "Licence: GET /product-pages?productType=licence. "
            "Funds: GET /micro-services/Funds. "
            "Guides: GET /micro-services/Product Insights (/business-guides). "
            "Live stated totals are scraped from marketing HTML for the completeness gate."
        ),
        "catalogs": [
            {
                "product_type": c["product_type"],
                "listing_url": c["listing_url"],
                "discovery": c["discovery"],
                "live_stated_total": c.get("live_stated_total"),
                "discovered": c.get("discovered"),
                "complete": c.get("complete"),
            }
            for c in catalogs
        ],
        "items": public_items,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["catalogs"], indent=2))
    print(f"Wrote {out} ({len(public_items)} packages)")

    incomplete = [c for c in catalogs if not c.get("complete")]
    if incomplete:
        print("INCOMPLETE vs live stated totals:", file=sys.stderr)
        for c in incomplete:
            print(
                f"  {c['product_type']}: discovered={c.get('discovered')} "
                f"live={c.get('live_stated_total')}",
                file=sys.stderr,
            )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
