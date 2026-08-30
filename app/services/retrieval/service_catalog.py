"""Deterministic answers for 'how many packages in <service>?' catalog questions."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models.schemas import RetrievedChunk
from app.utils.helpers import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GT_PATH = ROOT / "data" / "eval" / "service_packages_ground_truth.json"
DEFAULT_INDEX_PATH = ROOT / "config" / "indexes" / "service_catalog.json"

_COUNT_QUERY = re.compile(
    r"(?is)\b("
    r"how\s+many\s+(?:packages?|products?|skus?|services?)\b|"
    r"(?:number|count)\s+of\s+(?:packages?|products?)\b|"
    r"packages?\s+(?:are\s+)?(?:there|available|listed)\b|"
    r"list\s+(?:all\s+)?(?:the\s+)?packages?\b"
    r")"
)

# Display name / aliases ΓåÆ canonical display name used in GT
_SERVICE_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?is)\bpost[\s-]?setup\b"), "Post Setup Compliance"),
    (re.compile(r"(?is)\bvisa\b"), "Visa Services"),
    (re.compile(r"(?is)\battestat"), "Attestation"),
    (re.compile(r"(?is)\baccounting\b|\bbook\s*keep|\bbookeep"), "Accounting & Bookkeeping"),
    (re.compile(r"(?is)\blegal\b"), "Legal Services"),
    (re.compile(r"(?is)\bproduct\s+registration\b|\bcertification\b"), "Product Registration & Certification"),
    (re.compile(r"(?is)\bliquidat"), "Liquidation"),
    (re.compile(r"(?is)\bbanking\b"), "Banking Services"),
    (re.compile(r"(?is)\bwill\b|\bdrafting\b"), "Will Drafting Services"),
    (re.compile(r"(?is)\binsights?\b|\bbusiness\s+guides?\b"), "Product Insights & Guides"),
    (re.compile(r"(?is)\bfunds?\b"), "Funds"),
    (re.compile(r"(?is)\bother\s+services?\b"), "Other Services"),
]


def is_service_catalog_count_query(question: str) -> bool:
    return bool(_COUNT_QUERY.search(question or ""))


def match_service_name(question: str, known: list[str]) -> str | None:
    """Pick the best service label mentioned in the question."""
    text = question or ""
    # Prefer longest alias hit that maps to a known service
    hits: list[tuple[int, str]] = []
    for pattern, display in _SERVICE_ALIASES:
        if pattern.search(text) and display in known:
            hits.append((len(display), display))
    if hits:
        hits.sort(reverse=True)
        return hits[0][1]
    # Fallback: substring match on known names
    lowered = text.lower()
    for name in sorted(known, key=len, reverse=True):
        if name.lower() in lowered:
            return name
    return None


def _load_catalog(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed reading service catalog %s: %s", path, exc)
        return {}


@lru_cache(maxsize=4)
def load_service_catalog(
    index_path: str | None = None,
    gt_path: str | None = None,
) -> dict[str, Any]:
    """
    Returns {service_name: {"count": N, "packages": [{"name","slug",...}], "service_url": ...}}.
    Prefers compact index; falls back to scrape GT JSON.
    """
    idx = Path(index_path) if index_path else DEFAULT_INDEX_PATH
    data = _load_catalog(idx)
    services = data.get("services") if isinstance(data, dict) else None
    if isinstance(services, dict) and services:
        return services

    gt = Path(gt_path) if gt_path else DEFAULT_GT_PATH
    payload = _load_catalog(gt)
    packages = payload.get("packages") if isinstance(payload, dict) else None
    if not isinstance(packages, list):
        return {}

    out: dict[str, Any] = {}
    for row in packages:
        if not isinstance(row, dict):
            continue
        service = str(row.get("service") or "").strip()
        if not service:
            continue
        bucket = out.setdefault(
            service,
            {
                "count": 0,
                "packages": [],
                "service_url": row.get("service_url") or "",
            },
        )
        name = str(row.get("package_name") or row.get("package_slug") or "").strip()
        if name:
            bucket["packages"].append(
                {
                    "name": name,
                    "slug": row.get("package_slug"),
                    "id": row.get("package_id"),
                    "detail_url": row.get("package_detail_url"),
                }
            )
            bucket["count"] = len(bucket["packages"])
    return out


def export_service_catalog_index(
    gt_path: Path | str,
    out_path: Path | str | None = None,
) -> Path:
    """Write compact catalog index from scrape GT (for Lambda / fast lookup)."""
    gt = Path(gt_path)
    out = Path(out_path) if out_path else DEFAULT_INDEX_PATH
    payload = _load_catalog(gt)
    services: dict[str, Any] = {}
    for row in payload.get("packages") or []:
        if not isinstance(row, dict):
            continue
        service = str(row.get("service") or "").strip()
        if not service:
            continue
        bucket = services.setdefault(
            service,
            {
                "count": 0,
                "packages": [],
                "service_url": row.get("service_url") or "",
            },
        )
        name = str(row.get("package_name") or row.get("package_slug") or "").strip()
        if not name:
            continue
        bucket["packages"].append(
            {
                "name": name,
                "slug": row.get("package_slug"),
                "id": row.get("package_id"),
                "detail_url": row.get("package_detail_url"),
            }
        )
        bucket["count"] = len(bucket["packages"])

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "source": gt.as_posix(),
        "service_count": len(services),
        "package_count": sum(int(v.get("count") or 0) for v in services.values()),
        "services": services,
    }
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    load_service_catalog.cache_clear()
    logger.info(
        "Exported service catalog index: %d services, %d packages ΓåÆ %s",
        doc["service_count"],
        doc["package_count"],
        out,
    )
    return out


def build_catalog_count_answer(question: str) -> tuple[str, list[RetrievedChunk]] | None:
    """
    If the question is a package-count/list query for a known service, return a
    grounded answer + one synthetic chunk (bypasses vector retrieval).
    """
    if not is_service_catalog_count_query(question):
        return None
    catalog = load_service_catalog()
    if not catalog:
        return None
    service = match_service_name(question, list(catalog.keys()))
    if not service:
        return None
    info = catalog[service]
    count = int(info.get("count") or 0)
    packages = info.get("packages") or []
    names = [str(p.get("name") or "") for p in packages if p.get("name")]
    service_url = str(info.get("service_url") or "")

    want_list = bool(re.search(r"(?is)\blist\b", question or ""))
    lines = [
        f"Arnifi lists **{count}** packages under **{service}** "
        f"(from the public service catalog).",
    ]
    if want_list or count <= 40:
        lines.append("")
        lines.append("Packages:")
        for i, name in enumerate(names, start=1):
            lines.append(f"{i}. {name}")
    else:
        lines.append("")
        lines.append("Examples:")
        for name in names[:8]:
            lines.append(f"- {name}")
        if count > 8:
            lines.append(f"- ΓÇª and {count - 8} more on the {service} service page.")

    answer = "\n".join(lines)
    preview = "\n".join(names)
    chunk = RetrievedChunk(
        chunk_id=f"service-catalog|{service}|count",
        score=1.0,
        source_url=service_url or f"catalog://{service}",
        doc_title=f"{service} ΓÇö package catalog",
        heading_path="service_catalog > package_count",
        chunk_text=f"Service: {service}\nPackage count: {count}\nPackages:\n{preview}",
        metadata={
            "source_type": "website",
            "page_kind": "service_landing",
            "catalog_service": service,
            "catalog_section": "package_count",
            "lexical_rescue": True,
            "package_count": count,
        },
    )
    return answer, [chunk]
