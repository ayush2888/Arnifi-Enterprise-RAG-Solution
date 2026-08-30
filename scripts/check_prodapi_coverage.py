"""Task 5 ΓÇö Prodapi coverage check before ingest (listing vs detail vs sections).

HTML listing grids are Next.js shells; the authoritative package inventory and
section bodies live in prodapi. This script:

1. For every micro-service type: list count == successfully fetched detail count.
2. For every country (optional): licence list count == successfully fetched product count.
3. Section completeness: when CMS provides documentsRequired / processFlow / etc.,
   the extractor must produce the matching section path (no silent drop).

Exit code 1 on any mismatch so ingest jobs can gate on this.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ingestion.website_sections import (
    extract_micro_service_from_api,
    extract_setup_product_from_api,
)
from app.services.prodapi.client import (
    ProdapiClient,
    micro_service_page_url,
    setup_product_page_url,
)

# CMS field ΓåÆ expected heading_path[0] when the field is non-empty
_MICRO_FIELD_TO_PATH: tuple[tuple[str, str], ...] = (
    ("description", "introduction"),
    ("documentsRequired", "documents_required"),
    ("highlightsAndBenifits", "highlights_and_benefits"),
    ("processFlow", "process_flow"),
    ("faqs", "faqs"),
    ("termsAndConditions", "terms_and_conditions"),
)


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_micro_services(
    client: ProdapiClient,
    *,
    service_types: list[str] | None = None,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    types = service_types or client.list_service_types()
    report: dict[str, Any] = {"ok": True, "services": [], "errors": []}

    for stype in types:
        try:
            items = client.list_micro_services(stype)
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(f"list failed {stype}: {exc}")
            continue

        discovered = len(items)
        scraped = 0
        section_gaps: list[dict[str, Any]] = []
        fetch_failures: list[str] = []

        work = items if sample_limit is None else items[:sample_limit]
        for item in work:
            slug = str(item.get("slug") or "").strip()
            if not slug:
                fetch_failures.append("(missing slug)")
                continue
            try:
                detail = client.get_micro_service_detail(stype, slug)
            except Exception as exc:
                fetch_failures.append(f"{slug}: {exc}")
                continue
            scraped += 1
            url = micro_service_page_url(stype, slug, service_id=detail.get("id"))
            doc = extract_micro_service_from_api(detail, url)
            paths = {tuple(s.heading_path[:1]) for s in doc.sections if s.heading_path}
            flat = {p[0] for p in paths}

            missing: list[str] = []
            for field, path in _MICRO_FIELD_TO_PATH:
                raw = detail.get(field)
                if field == "description":
                    present = _nonempty_text(raw) or _nonempty_text(
                        detail.get("shortDescription")
                    )
                else:
                    present = _nonempty_list(raw)
                if present and path not in flat:
                    missing.append(path)
            if missing:
                section_gaps.append(
                    {
                        "slug": slug,
                        "title": detail.get("title"),
                        "missing_sections": missing,
                        "documentsRequired_count": len(
                            detail.get("documentsRequired") or []
                        ),
                    }
                )

        # When sampling, only compare scraped vs sample size; full runs compare to discovered.
        expected = discovered if sample_limit is None else len(work)
        match = scraped == expected and not fetch_failures and not section_gaps
        if not match:
            report["ok"] = False
            report["errors"].append(
                f"{stype}: discovered={discovered} scraped={scraped}/{expected} "
                f"fetch_failures={len(fetch_failures)} section_gaps={len(section_gaps)}"
            )

        report["services"].append(
            {
                "service_type": stype,
                "discovered": discovered,
                "scraped": scraped,
                "expected": expected,
                "fetch_failures": fetch_failures,
                "section_gaps": section_gaps,
                "ok": match,
            }
        )

    return report


def check_licences(
    client: ProdapiClient,
    *,
    country_limit: int | None = None,
    sample_per_country: int | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {"ok": True, "countries": [], "errors": []}
    countries = client.list_countries()
    if country_limit is not None:
        countries = countries[:country_limit]

    for country in countries:
        api_slug = str(country.get("slug") or "").strip()
        country_id = country.get("id")
        name = str(country.get("name") or api_slug)
        if country_id is None:
            continue
        try:
            licences = client.list_licence_packages_for_country(country_id)
        except Exception as exc:
            report["ok"] = False
            report["errors"].append(f"licence list failed {name}: {exc}")
            continue

        discovered = len(licences)
        work = licences if sample_per_country is None else licences[:sample_per_country]
        scraped = 0
        fetch_failures: list[str] = []
        for row in work:
            pid = row.get("id") if isinstance(row, dict) else None
            if pid is None:
                fetch_failures.append("(missing id)")
                continue
            try:
                detail = client.get_product_page_by_id(pid)
            except Exception as exc:
                fetch_failures.append(f"id={pid}: {exc}")
                continue
            if not detail:
                fetch_failures.append(f"id={pid}: empty")
                continue
            scraped += 1
            # Ensure extractor does not crash
            attrs = (
                detail.get("attributes")
                if isinstance(detail.get("attributes"), dict)
                else detail
            )
            slug = str(attrs.get("slug") or api_slug)
            extract_setup_product_from_api(
                detail, setup_product_page_url(slug, product_id=pid)
            )

        expected = discovered if sample_per_country is None else len(work)
        match = scraped == expected and not fetch_failures
        if not match:
            report["ok"] = False
            report["errors"].append(
                f"{name}: discovered={discovered} scraped={scraped}/{expected} "
                f"failures={len(fetch_failures)}"
            )
        report["countries"].append(
            {
                "country": name,
                "discovered": discovered,
                "scraped": scraped,
                "expected": expected,
                "fetch_failures": fetch_failures,
                "ok": match,
            }
        )
    return report


def smoke_singapore_statutory_audit(client: ProdapiClient) -> dict[str, Any]:
    """One package end-to-end: must expose all 15 Documents Required lines."""
    detail = client.get_micro_service_detail(
        "Accounting & Bookkeeping", "singapore-statutory-audit"
    )
    cms_docs = detail.get("documentsRequired") or []
    url = micro_service_page_url(
        "Accounting & Bookkeeping",
        "singapore-statutory-audit",
        service_id=detail.get("id"),
    )
    doc = extract_micro_service_from_api(detail, url)
    docs_secs = [
        s for s in doc.sections if s.heading_path and s.heading_path[0] == "documents_required"
    ]
    text = docs_secs[0].text if docs_secs else ""
    numbered = sum(1 for line in text.splitlines() if line.strip()[:2].rstrip(".").isdigit() or line.strip()[:3].split(".")[0].isdigit())
    # Count "N. " prefixes
    import re

    numbered = len(re.findall(r"(?m)^\d+\.\s+", text))
    ok = len(cms_docs) == 15 and numbered == 15 and "Certificate of Incorporation" in text
    return {
        "ok": ok,
        "cms_documentsRequired": len(cms_docs),
        "extracted_numbered_lines": numbered,
        "has_certificate": "Certificate of Incorporation" in text,
        "snippet": text[:280],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prodapi coverage check (Task 5)")
    parser.add_argument(
        "--services-only",
        action="store_true",
        help="Skip country licence checks",
    )
    parser.add_argument(
        "--licences-only",
        action="store_true",
        help="Skip micro-service checks",
    )
    parser.add_argument(
        "--service-type",
        action="append",
        default=None,
        help="Limit to one or more micro-service types (repeatable)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Only fetch first N packages per listing (smoke)",
    )
    parser.add_argument(
        "--country-limit",
        type=int,
        default=None,
        help="Only check first N countries",
    )
    parser.add_argument(
        "--smoke-sg-audit",
        action="store_true",
        help="Also assert Singapore Statutory Audit has 15 documents",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON report path",
    )
    args = parser.parse_args()

    client = ProdapiClient()
    out: dict[str, Any] = {"ok": True}

    if not args.licences_only:
        micro = check_micro_services(
            client,
            service_types=args.service_type,
            sample_limit=args.sample,
        )
        out["micro_services"] = micro
        out["ok"] = out["ok"] and micro["ok"]

    if not args.services_only:
        licences = check_licences(
            client,
            country_limit=args.country_limit,
            sample_per_country=args.sample,
        )
        out["licences"] = licences
        out["ok"] = out["ok"] and licences["ok"]

    if args.smoke_sg_audit:
        smoke = smoke_singapore_statutory_audit(client)
        out["smoke_singapore_statutory_audit"] = smoke
        out["ok"] = out["ok"] and smoke["ok"]

    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}")
    print(text)
    if out["ok"]:
        print("COVERAGE OK")
        return 0
    print("COVERAGE FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
