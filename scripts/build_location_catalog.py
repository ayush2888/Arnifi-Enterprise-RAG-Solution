"""Build location catalog index with full licence lists + country-overview funds."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.prodapi.client import ProdapiClient
from app.services.retrieval.location_catalog import (
    DEFAULT_INDEX_PATH,
    export_location_catalog_from_live,
    load_location_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh location catalog (full packages + funds) from prodapi"
    )
    parser.add_argument("--out", default=str(DEFAULT_INDEX_PATH))
    args = parser.parse_args()

    client = ProdapiClient()
    out = export_location_catalog_from_live(client, out_path=args.out)
    load_location_catalog.cache_clear()
    catalog = load_location_catalog(str(out))
    print(f"Wrote {out}")
    print(f"{'Country':<40} {'Funds':>6} {'Packages':>8}")
    for name in sorted(catalog.keys()):
        info = catalog[name]
        print(f"{name:<40} {info['fund_count']:>6} {info['package_count']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
