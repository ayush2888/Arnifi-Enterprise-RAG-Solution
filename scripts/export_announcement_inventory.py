#!/usr/bin/env python
"""Export Press / Events / Case Studies inventory under data/eval/.

Usage:
  python scripts/export_announcement_inventory.py
  python scripts/export_announcement_inventory.py --press-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ingestion.announcement_content import (
    discover_case_studies,
    discover_events,
    discover_press_releases,
    inventory_all,
)
from app.services.prodapi.client import ProdapiClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--press-only", action="store_true")
    parser.add_argument("--events-only", action="store_true")
    parser.add_argument("--case-studies-only", action="store_true")
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "eval" / "announcement_inventory.json"),
    )
    args = parser.parse_args()

    exclusive = sum(
        bool(x)
        for x in (args.press_only, args.events_only, args.case_studies_only)
    )
    if exclusive > 1:
        print("Pass at most one stream flag", file=sys.stderr)
        return 2

    client = ProdapiClient()
    if args.press_only:
        report = {"press_releases": discover_press_releases()}
    elif args.events_only:
        report = {"events": discover_events(archived_only=True)}
    elif args.case_studies_only:
        report = {"case_studies": discover_case_studies(client)}
    else:
        report = inventory_all(client)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nWrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
