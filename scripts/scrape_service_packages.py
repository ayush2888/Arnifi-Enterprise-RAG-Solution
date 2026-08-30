"""Scrape the 10 Arnifi service catalogs into structured package records (Step 1).

Landing HTML only shows package *cards* (Next.js shell). The five package
sections live in the same CMS the site uses: public prodapi micro-services.
Product Insights packages are still fetched the same way; empty process_flow
is preserved and content_type marks them as guides.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.prodapi.client import ProdapiClient, micro_service_page_url
from app.utils.helpers import clean_text

# Prompt service label ΓåÆ (prodapi type, public service landing URL)
SERVICE_CATALOG: list[tuple[str, str, str]] = [
    (
        "Post Setup Compliance",
        "Post-Setup Compliance",
        "https://arnifi.com/services/post-setup-compliance",
    ),
    (
        "Visa Services",
        "Visa Services",
        "https://arnifi.com/services/visa-service",
    ),
    (
        "Attestation",
        "Attestation",
        "https://arnifi.com/services/attestations",
    ),
    (
        "Accounting & Bookkeeping",
        "Accounting & Bookkeeping",
        "https://arnifi.com/services/accounting",
    ),
    (
        "Legal Services",
        "Legal Service",
        "https://arnifi.com/services/legal-services",
    ),
    (
        "Product Registration & Certification",
        "Product Registration",
        "https://arnifi.com/services/product-registration",
    ),
    (
        "Liquidation",
        "Liquidation",
        "https://arnifi.com/services/liquidation",
    ),
    (
        "Banking Services",
        "Banking Services",
        "https://arnifi.com/services/banking-services",
    ),
    (
        "Will Drafting Services",
        "Will Drafting",
        "https://arnifi.com/services/will-drafting",
    ),
    (
        "Product Insights & Guides",
        "Product Insights",
        "https://arnifi.com/business-guides",
    ),
    (
        "Funds",
        "Funds",
        "https://arnifi.com/services/funds",
    ),
    (
        "Other Services",
        "Other Services",
        "https://arnifi.com/services/other-services",
    ),
]

DEFAULT_OUT = ROOT / "data" / "eval" / "service_packages_ground_truth.json"

SECTION_KEYS = (
    "introduction",
    "documents_required",
    "highlights_and_benefits",
    "process_flow",
    "faqs",
    "terms_and_conditions",
)


def _blocks_to_text(items: Any, *, style: str = "heading_body") -> str:
    if not isinstance(items, list) or not items:
        return ""
    lines: list[str] = []
    for i, item in enumerate(items, start=1):
        if isinstance(item, str):
            text = clean_text(item)
            if text:
                lines.append(text)
            continue
        if not isinstance(item, dict):
            continue
        heading = clean_text(
            str(
                item.get("heading")
                or item.get("question")
                or item.get("title")
                or item.get("termAndCondition")
                or item.get("name")
                or ""
            )
        )
        body = clean_text(
            str(
                item.get("description")
                or item.get("answer")
                or item.get("termAndCondition")
                or item.get("text")
                or item.get("value")
                or ""
            )
        )
        if style == "documents":
            label = heading or body
            if not label:
                continue
            if body and body != label and label not in body:
                lines.append(f"{i}. {label}: {body}")
            else:
                lines.append(f"{i}. {label}")
            continue
        if style == "faq" and heading and body:
            lines.append(f"Q: {heading}\nA: {body}")
        elif style == "process" and heading:
            lines.append(f"{i}. {heading}: {body}" if body else f"{i}. {heading}")
        elif heading and body:
            if heading in body:
                lines.append(body)
            else:
                lines.append(f"{heading}\n{body}")
        elif body:
            lines.append(body)
        elif heading:
            lines.append(heading)
    return "\n\n".join(lines).strip()


def payload_to_record(
    *,
    service: str,
    service_url: str,
    payload: dict[str, Any],
    content_type: str,
) -> dict[str, Any]:
    package_name = clean_text(str(payload.get("title") or payload.get("slug") or ""))
    slug = clean_text(str(payload.get("slug") or ""))
    package_id = payload.get("id")
    detail_url = micro_service_page_url(
        str(payload.get("type") or service),
        slug,
        service_id=package_id,
    )
    sections = {
        "introduction": clean_text(
            str(payload.get("description") or payload.get("shortDescription") or "")
        ),
        "documents_required": _blocks_to_text(
            payload.get("documentsRequired"), style="documents"
        ),
        "highlights_and_benefits": _blocks_to_text(payload.get("highlightsAndBenifits")),
        "process_flow": _blocks_to_text(payload.get("processFlow"), style="process"),
        "faqs": _blocks_to_text(payload.get("faqs"), style="faq"),
        "terms_and_conditions": _blocks_to_text(payload.get("termsAndConditions")),
    }
    nonempty = [k for k in SECTION_KEYS if sections[k]]
    return {
        "service": service,
        "service_url": service_url,
        "package_name": package_name,
        "package_slug": slug,
        "package_id": package_id,
        "package_detail_url": detail_url,
        "content_type": content_type,
        "currency": clean_text(str(payload.get("currency") or "")),
        "starting_price": payload.get("startingPrice")
        or payload.get("price")
        or payload.get("currentPrice"),
        "sections": sections,
        "sections_present": nonempty,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape_all(client: ProdapiClient | None = None) -> dict[str, Any]:
    client = client or ProdapiClient()
    records: list[dict[str, Any]] = []
    per_service: dict[str, int] = {}
    notes: list[str] = []

    for service, api_type, service_url in SERVICE_CATALOG:
        items = client.list_micro_services(api_type)
        content_type = "guide" if api_type == "Product Insights" else "service_package"
        count = 0
        for item in items:
            slug = str(item.get("slug") or "").strip()
            if not slug:
                continue
            detail = client.get_micro_service_detail(api_type, slug)
            rec = payload_to_record(
                service=service,
                service_url=service_url,
                payload=detail,
                content_type=content_type,
            )
            if content_type == "guide" and not rec["sections"]["process_flow"]:
                notes.append(
                    f"{service} / {rec['package_name']}: process_flow empty "
                    "(guide-style package; still stored under 5-section schema)."
                )
            records.append(rec)
            count += 1
        per_service[service] = count

    return {
        "source": "prodapi.arnifi.com/api/micro-services",
        "rationale": (
            "Service landing HTML is a Next.js shell with package cards only; "
            "the five sections are loaded from prodapi package detail (same CMS)."
        ),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "service_count": len(SERVICE_CATALOG),
        "package_count": len(records),
        "packages_per_service": per_service,
        "expected_base_chunks": len(records) * len(SECTION_KEYS),
        "notes": notes,
        "packages": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 1: scrape service packages GT")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output JSON path for ground-truth package records",
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Scraping {len(SERVICE_CATALOG)} services via prodapi micro-servicesΓÇª")
    payload = scrape_all()
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {out}")
    print(f"Packages: {payload['package_count']}")
    for name, n in payload["packages_per_service"].items():
        print(f"  {name}: {n}")
    print(f"Expected base section slots (packages x 5): {payload['expected_base_chunks']}")
    if payload["notes"]:
        print(f"Notes: {len(payload['notes'])} (see JSON)")

    try:
        from app.services.retrieval.service_catalog import export_service_catalog_index

        idx = export_service_catalog_index(out)
        print(f"Catalog index: {idx}")
    except Exception as exc:
        print(f"Catalog index export skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
