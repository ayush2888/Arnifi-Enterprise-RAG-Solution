"""Deterministic answers for 'how many funds/packages in <country>?' questions."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models.schemas import RetrievedChunk
from app.services.prodapi.client import country_page_url
from app.utils.helpers import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GT_DIR = ROOT / "data" / "eval" / "country_ground_truth"
DEFAULT_INDEX_PATH = ROOT / "config" / "indexes" / "location_catalog.json"

_COUNT_QUERY = re.compile(
    r"(?is)\b("
    r"how\s+many\s+(?:funds?|packages?|products?|licen[cs]es?)\b|"
    r"(?:number|count)\s+of\s+(?:funds?|packages?)\b|"
    r"(?:funds?|packages?)\s+(?:are\s+)?(?:there|available|listed)\b|"
    r"list\s+(?:all\s+)?(?:the\s+)?(?:top\s+)?(?:funds?|packages?)\b"
    r")"
)

_FUNDS_CUE = re.compile(r"(?is)\bfunds?\b")
_PACKAGES_CUE = re.compile(r"(?is)\bpackages?|licen[cs]es?|products?\b")

# Country name / alias ΓåÆ canonical countryName from catalog
_COUNTRY_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?is)\bhong\s*kong\b"), "Hong Kong"),
    (re.compile(r"(?is)\bsaint\s+vincent|\bst\.?\s*vincent|\bgrenadines\b"), "Saint Vincent and the Grenadines"),
    (re.compile(r"(?is)\bbritish\s+virgin|\bbvi\b"), "British Virgin Islands"),
    (re.compile(r"(?is)\bcayman\b"), "Cayman Island"),
    (re.compile(r"(?is)\bsaudi\b|\bksa\b|kingdom\s+of\s+saudi"), "Saudi Arabia"),
    (re.compile(r"(?is)\bunited\s+arab|\buae\b"), "UAE"),
    (re.compile(r"(?is)\bunited\s+kingdom\b|\b\buk\b|\bgreat\s+britain\b"), "United Kingdom"),
    (re.compile(r"(?is)\bpuerto\s*rico\b"), "Puerto Rico"),
    (re.compile(r"(?is)\bluxembourg\b"), "Luxembourg"),
    (re.compile(r"(?is)\bsingapore\b"), "Singapore"),
    (re.compile(r"(?is)\bmauritius\b"), "Mauritius"),
    (re.compile(r"(?is)\bcyprus\b"), "Cyprus"),
    (re.compile(r"(?is)\bireland\b"), "Ireland"),
    (re.compile(r"(?is)\bmalaysia\b"), "Malaysia"),
    (re.compile(r"(?is)\bguernsey\b"), "Guernsey"),
]


def is_location_catalog_count_query(question: str) -> bool:
    return bool(_COUNT_QUERY.search(question or ""))


def match_country_name(question: str, known: list[str]) -> str | None:
    text = question or ""
    for pattern, display in _COUNTRY_ALIASES:
        if pattern.search(text) and display in known:
            return display
    lowered = text.lower()
    for name in sorted(known, key=len, reverse=True):
        if name.lower() in lowered:
            return name
    return None


def _kind_from_question(question: str) -> str:
    """Return 'funds', 'packages', or 'both'."""
    q = question or ""
    wants_funds = bool(_FUNDS_CUE.search(q))
    wants_packages = bool(_PACKAGES_CUE.search(q))
    if wants_funds and not wants_packages:
        return "funds"
    if wants_packages and not wants_funds:
        return "packages"
    # "how many funds or packages" / ambiguous ΓåÆ prefer funds if country is fund-heavy later
    if wants_funds:
        return "funds"
    return "packages"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed reading %s: %s", path, exc)
        return {}


def _item_title(row: dict[str, Any], *, funds: bool) -> str:
    if funds:
        return str(row.get("title") or row.get("slug") or "").strip()
    return str(
        row.get("productName") or row.get("title") or row.get("slug") or ""
    ).strip()


@lru_cache(maxsize=4)
def load_location_catalog(
    index_path: str | None = None,
    gt_dir: str | None = None,
) -> dict[str, Any]:
    """
    {countryName: {shortcode, source_url, funds: [...], packages: [...], fund_count, package_count}}
    """
    idx = Path(index_path) if index_path else DEFAULT_INDEX_PATH
    data = _load_json(idx)
    countries = data.get("countries") if isinstance(data, dict) else None
    if isinstance(countries, dict) and countries:
        return countries

    folder = Path(gt_dir) if gt_dir else DEFAULT_GT_DIR
    out: dict[str, Any] = {}
    if not folder.exists():
        return out
    for path in sorted(folder.glob("*.json")):
        payload = _load_json(path)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        overview = (
            payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
        )
        name = str(
            meta.get("countryName") or overview.get("title") or path.stem
        ).strip()
        shortcode = str(meta.get("shortcode") or "").strip()
        funds = []
        for row in overview.get("funds") or []:
            if not isinstance(row, dict):
                continue
            title = _item_title(row, funds=True)
            if title:
                funds.append(
                    {
                        "name": title,
                        "slug": row.get("slug"),
                        "id": row.get("id"),
                        "price": row.get("startingPrice"),
                        "currency": row.get("currency"),
                    }
                )
        packages = []
        for row in overview.get("productPages") or []:
            if not isinstance(row, dict):
                continue
            title = _item_title(row, funds=False)
            if title:
                packages.append(
                    {
                        "name": title,
                        "slug": row.get("slug"),
                        "id": row.get("id"),
                        "price": row.get("startingArnifiPrice")
                        or row.get("startingPrice"),
                        "currency": row.get("currency"),
                    }
                )
        out[name] = {
            "shortcode": shortcode,
            "source_url": country_page_url(shortcode, slug=str(meta.get("slug") or name)),
            "funds": funds,
            "packages": packages,
            "fund_count": len(funds),
            "package_count": len(packages),
        }
    return out


def export_location_catalog_from_live(
    client: Any,
    *,
    out_path: Path | str | None = None,
) -> Path:
    """
    Build the authoritative location catalog from prodapi:

    - packages: full /product-pages licence list per country (UAE 78, KSA 6, ΓÇª)
    - funds: country-overview `funds` array (Luxembourg/Guernsey Top Funds)

    Country-overview `productPages` is only a short Top Packages strip ΓÇö do not
    use it for ΓÇ£how many packagesΓÇ¥ answers.
    """
    out = Path(out_path) if out_path else DEFAULT_INDEX_PATH
    countries_meta = client.list_countries()
    rebuilt: dict[str, Any] = {}

    for meta in countries_meta:
        if not isinstance(meta, dict):
            continue
        name = str(meta.get("countryName") or "").strip()
        slug = str(meta.get("slug") or "").strip()
        shortcode = str(meta.get("shortcode") or "").strip()
        country_id = meta.get("id")
        if not name or country_id is None:
            continue

        overview = client.get_country_overview(slug)
        funds: list[dict[str, Any]] = []
        for row in overview.get("funds") or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("slug") or "").strip()
            if not title:
                continue
            funds.append(
                {
                    "name": title,
                    "slug": row.get("slug"),
                    "id": row.get("id"),
                    "price": row.get("startingPrice"),
                    "currency": row.get("currency"),
                }
            )

        packages: list[dict[str, Any]] = []
        try:
            rows = client.list_licence_packages_for_country(country_id)
        except Exception as exc:
            logger.error("Failed listing licences for %s (%s): %s", name, country_id, exc)
            rows = []
        for row in rows:
            attrs = (
                row.get("attributes") if isinstance(row.get("attributes"), dict) else row
            )
            if not isinstance(attrs, dict):
                continue
            title = str(
                attrs.get("productName") or attrs.get("title") or attrs.get("slug") or ""
            ).strip()
            if not title:
                continue
            packages.append(
                {
                    "name": title,
                    "slug": attrs.get("slug"),
                    "id": row.get("id") or attrs.get("id"),
                    "price": attrs.get("startingArnifiPrice")
                    or attrs.get("startingPrice"),
                    "currency": attrs.get("currency"),
                }
            )

        rebuilt[name] = {
            "shortcode": shortcode,
            "api_slug": slug,
            "country_id": country_id,
            "source_url": country_page_url(shortcode, slug=slug),
            "funds": funds,
            "packages": packages,
            "fund_count": len(funds),
            "package_count": len(packages),
            "top_packages_on_country_page": len(overview.get("productPages") or []),
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "source": "prodapi country-overview funds + product-pages licences",
        "country_count": len(rebuilt),
        "countries": rebuilt,
    }
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    load_location_catalog.cache_clear()
    logger.info(
        "Exported live location catalog: %d countries ΓåÆ %s",
        len(rebuilt),
        out,
    )
    return out


def export_location_catalog_index(
    gt_dir: Path | str | None = None,
    out_path: Path | str | None = None,
) -> Path:
    """
    Prefer live prodapi full licence lists. Falls back to country GT top cards
    only if live export is unavailable.
    """
    try:
        from app.services.prodapi.client import ProdapiClient

        return export_location_catalog_from_live(
            ProdapiClient(), out_path=out_path or DEFAULT_INDEX_PATH
        )
    except Exception as exc:
        logger.warning("Live location catalog export failed (%s); using GT fallback", exc)

    folder = Path(gt_dir) if gt_dir else DEFAULT_GT_DIR
    out = Path(out_path) if out_path else DEFAULT_INDEX_PATH
    rebuilt: dict[str, Any] = {}
    for path in sorted(folder.glob("*.json")):
        payload = _load_json(path)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        overview = (
            payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
        )
        name = str(
            meta.get("countryName") or overview.get("title") or path.stem
        ).strip()
        shortcode = str(meta.get("shortcode") or "").strip()
        funds = []
        for row in overview.get("funds") or []:
            if isinstance(row, dict) and _item_title(row, funds=True):
                funds.append(
                    {
                        "name": _item_title(row, funds=True),
                        "slug": row.get("slug"),
                        "id": row.get("id"),
                        "price": row.get("startingPrice"),
                        "currency": row.get("currency"),
                    }
                )
        packages = []
        for row in overview.get("productPages") or []:
            if isinstance(row, dict) and _item_title(row, funds=False):
                packages.append(
                    {
                        "name": _item_title(row, funds=False),
                        "slug": row.get("slug"),
                        "id": row.get("id"),
                        "price": row.get("startingArnifiPrice")
                        or row.get("startingPrice"),
                        "currency": row.get("currency"),
                    }
                )
        rebuilt[name] = {
            "shortcode": shortcode,
            "api_slug": str(meta.get("slug") or name),
            "source_url": country_page_url(shortcode, slug=str(meta.get("slug") or name)),
            "funds": funds,
            "packages": packages,
            "fund_count": len(funds),
            "package_count": len(packages),
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "source": folder.as_posix(),
        "country_count": len(rebuilt),
        "countries": rebuilt,
    }
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    load_location_catalog.cache_clear()
    logger.info(
        "Exported location catalog index (GT fallback): %d countries ΓåÆ %s",
        len(rebuilt),
        out,
    )
    return out


def build_location_catalog_count_answer(
    question: str,
) -> tuple[str, list[RetrievedChunk]] | None:
    if not is_location_catalog_count_query(question):
        return None
    catalog = load_location_catalog()
    if not catalog:
        return None
    country = match_country_name(question, list(catalog.keys()))
    if not country:
        return None

    info = catalog[country]
    kind = _kind_from_question(question)
    if kind == "funds":
        items = info.get("funds") or []
        label = "funds"
        count_key = "fund_count"
        scope = "on the country page Explore Funds list"
    else:
        items = info.get("packages") or []
        label = "packages"
        count_key = "package_count"
        scope = "in the full licence/setup catalog for this country"
        # Fund hubs (Luxembourg/Guernsey): packages list may be empty
        if not items and (info.get("funds") or []):
            items = info.get("funds") or []
            label = "funds"
            count_key = "fund_count"
            scope = "on the country page Explore Funds list"

    count = int(info.get(count_key) or len(items))
    source_url = str(info.get("source_url") or "")
    names = [str(i.get("name") or "") for i in items if i.get("name")]
    want_list = bool(re.search(r"(?is)\blist\b", question or ""))
    show_n = len(names) if (want_list or count <= 12) else min(8, len(names))

    lines = [
        f"Arnifi lists **{count}** {label} for **{country}** "
        f"({scope}).",
        "",
    ]
    if names:
        lines.append(f"{label.capitalize()}:")
        for i, name in enumerate(names[:show_n], start=1):
            price = None
            currency = ""
            if i - 1 < len(items):
                price = items[i - 1].get("price")
                currency = str(items[i - 1].get("currency") or "")
            if price not in (None, "", 0, "0"):
                lines.append(f"{i}. {name} ΓÇö starting from {currency} {price}".strip())
            else:
                lines.append(f"{i}. {name}")
        if count > show_n:
            lines.append(f"- ΓÇª and {count - show_n} more.")
    answer = "\n".join(lines)
    preview = "\n".join(names)
    chunk = RetrievedChunk(
        chunk_id=f"location-catalog|{country}|{label}",
        score=1.0,
        source_url=source_url or f"catalog://{country}",
        doc_title=f"{country} ΓÇö {label} catalog",
        heading_path=f"location_catalog > {label}",
        chunk_text=f"Country: {country}\n{label} count: {count}\n{preview}",
        metadata={
            "source_type": "website",
            "page_kind": "country_overview",
            "catalog_section": "top_funds" if label == "funds" else "top_packages",
            "lexical_rescue": True,
            "fund_count" if label == "funds" else "package_count": count,
        },
    )
    return answer, [chunk]
