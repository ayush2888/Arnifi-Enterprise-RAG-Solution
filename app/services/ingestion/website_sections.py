"""Section-aware extractors for Arnifi catalog pages.

Country pages, service landings, and package details are split the way
the site is structured (FAQ pair, selling point, documents required)
instead of one blob per URL.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.models.schemas import Document, Section
from app.utils.helpers import canonicalize_url, clean_text

PACKAGE_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "introduction": ("introduction", "intro", "about this", "overview"),
    "documents_required": (
        "document required",
        "documents required",
        "documents needed",
        "required document",
        "supporting document",
    ),
    "highlights": ("highlight", "benefit"),
    "process_flow": ("process", "how it works", "application process", "step"),
    "faq": ("faq", "frequently asked"),
    "terms": ("term", "condition"),
    "pricing": ("pricing", "price", "fee", "starting from", "cost"),
    "resources": ("resource", "guide"),
    "office_availability": ("office", "availability"),
}

EXPECTED_PACKAGE_SECTIONS = (
    "introduction",
    "documents_required",
    "highlights",
    "process_flow",
    "faq",
    "terms",
    "pricing",
)

_COUNTRY_MARKERS: tuple[tuple[str, str], ...] = (
    ("recent market insights", "market_insights"),
    ("key selling points", "selling_points"),
    ("country overview", "overview"),
    ("wondering what our application process", "process_flow"),
    ("application process", "process_flow"),
    ("faq", "faq"),
)

# Short country path -> product-details freezone slug
_SHORT_COUNTRY_SLUGS: dict[str, str] = {
    "/ae": "uae",
    "/sa": "saudi-arabia",
    "/ksa": "saudi-arabia",
    "/sg": "singapore",
    "/hk": "hong-kong",
    "/my": "malaysia",
    "/cy": "cyprus",
    "/ie": "ireland",
    "/lu": "luxembourg",
    "/mu": "mauritius",
    "/gg": "guernsey",
    "/vg": "british-virgin-islands",
    "/bvi": "british-virgin-islands",
    "/ky": "cayman-islands",
    "/pr": "puerto-rico",
    "/vc": "saint-vincent-and-the-grenadines",
    "/svg": "saint-vincent-and-the-grenadines",
    "/uk": "united-kingdom",
    "/gb": "united-kingdom",
}


def _unescape_next_flight(text: str) -> str:
    """Turn Next.js flight-escaped JSON fragments into normal quotes."""
    return (
        text.replace("\\\\n", "\n")
        .replace("\\u0026", "&")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _country_slug_from_url(source_url: str, authority_name: str | None = None) -> str:
    from urllib.parse import urlparse

    path = urlparse(source_url).path.rstrip("/") or "/"
    if path in _SHORT_COUNTRY_SLUGS:
        return _SHORT_COUNTRY_SLUGS[path]
    if path.startswith("/country-overview/"):
        return path.split("/")[-1].lower()
    if authority_name:
        return re.sub(r"[^a-z0-9]+", "-", authority_name.lower()).strip("-")
    return "unknown"


def parse_country_embedded_catalog(html: str, source_url: str) -> dict[str, Any]:
    """Pull funds / packages / FAQ / process from country-page RSC payloads."""
    norm = _unescape_next_flight(html)
    funds: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []

    fund_re = re.compile(
        r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"title"\s*:\s*"([^"]+)"\s*,\s*"slug"\s*:\s*"([^"]+)"\s*,'
        r'\s*"startingPrice"\s*:\s*(\d+)\s*,\s*"currency"\s*:\s*"([A-Z]+)"'
        r'(?:.*?)"authorityName"\s*:\s*"([^"]+)"',
        re.S,
    )
    seen_slugs: set[str] = set()
    for m in fund_re.finditer(norm):
        slug = m.group(3)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        funds.append(
            {
                "id": int(m.group(1)),
                "title": clean_text(m.group(2)),
                "slug": slug,
                "starting_price": int(m.group(4)),
                "currency": m.group(5),
                "authority_name": clean_text(m.group(6)),
                "kind": "funds",
            }
        )

    # Licence / package cards sometimes use productName instead of title.
    pkg_re = re.compile(
        r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"productName"\s*:\s*"([^"]+)"\s*,\s*"slug"\s*:\s*"([^"]+)"'
        r'(?:.*?"startingPrice"\s*:\s*(\d+))?(?:.*?"currency"\s*:\s*"([A-Z]+)")?',
        re.S,
    )
    for m in pkg_re.finditer(norm):
        slug = m.group(3)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        packages.append(
            {
                "id": int(m.group(1)),
                "title": clean_text(m.group(2)),
                "slug": slug,
                "starting_price": int(m.group(4) or 0),
                "currency": m.group(5) or "",
                "authority_name": "",
                "kind": "licence",
            }
        )

    faqs: list[tuple[str, str]] = []
    for m in re.finditer(
        r'"question"\s*:\s*"([^"]+)"\s*,\s*"answer"\s*:\s*"([^"]+)"',
        norm,
    ):
        q, a = clean_text(m.group(1)), clean_text(m.group(2))
        # Skip process steps mistakenly shaped as Q/A when answer looks like a step body
        # but keep real FAQs (questions usually end with ?).
        if q.endswith("?") and len(a) >= 8:
            faqs.append((q, a))

    process_steps: list[tuple[str, str]] = []
    # processStep objects use question/answer without trailing ?
    for m in re.finditer(
        r'"processStep"\s*:\s*\[(.*?)\]\s*,\s*"productPages"',
        norm,
        re.S,
    ):
        block = m.group(1)
        for sm in re.finditer(
            r'"question"\s*:\s*"([^"]+)"\s*,\s*"answer"\s*:\s*"([^"]+)"',
            block,
        ):
            process_steps.append((clean_text(sm.group(1)), clean_text(sm.group(2))))
        break
    if not process_steps:
        # Fallback: numbered application steps often appear as question/answer without ?
        for m in re.finditer(
            r'"question"\s*:\s*"(Select Your Business Structure|Complete Company Registration|'
            r'Establish Operational Framework|Maintain Regulatory Compliance|'
            r'Define Business Setup|Set Up Operational Requirements|'
            r'Maintain Ongoing Compliance)[^"]*"\s*,\s*"answer"\s*:\s*"([^"]+)"',
            norm,
            re.I,
        ):
            process_steps.append((clean_text(m.group(1)), clean_text(m.group(2))))

    insights: list[tuple[str, str]] = []
    for m in re.finditer(
        r'"title"\s*:\s*"([^"]+)"\s*,\s*"text"\s*:\s*"([^"]+)"\s*,\s*"tag"\s*:\s*"([^"]*)"\s*,\s*"date"\s*:\s*"([^"]*)"',
        norm,
    ):
        insights.append((clean_text(m.group(1)), clean_text(m.group(2))))

    selling: list[str] = []
    for m in re.finditer(r'"keySellingPoints"\s*:\s*\[(.*?)\]\s*,\s*"prosCons"', norm, re.S):
        for sm in re.finditer(r'"value"\s*:\s*"([^"]+)"', m.group(1)):
            selling.append(clean_text(sm.group(1)))
        break

    overview = ""
    for m in re.finditer(r'"overviewDetail"[^}]*?"(?:text|value|content)"\s*:\s*"([^"]{40,})"', norm):
        overview = clean_text(m.group(1))
        break
    if not overview:
        # Sometimes overview is a long prose string near Country Overview markers in visible HTML only.
        overview = ""

    country_slug = _country_slug_from_url(
        source_url,
        funds[0]["authority_name"] if funds else None,
    )
    detail_urls: list[str] = []
    for item in funds + packages:
        kind = "funds" if item["kind"] == "funds" else "business-setup"
        slug_country = country_slug
        if item.get("authority_name") and item["kind"] == "funds":
            slug_country = re.sub(r"[^a-z0-9]+", "-", item["authority_name"].lower()).strip("-")
        detail_urls.append(
            canonicalize_url(
                f"https://arnifi.com/product-details/{kind}/{slug_country}/{item['slug']}/{item['id']}"
            )
        )

    return {
        "funds": funds,
        "packages": packages,
        "faqs": faqs,
        "process_steps": process_steps,
        "insights": insights,
        "selling_points": selling,
        "overview": overview,
        "country_slug": country_slug,
        "detail_urls": detail_urls,
    }


def country_catalog_detail_urls(html: str, source_url: str) -> set[str]:
    """Product-detail URLs for Explore Funds / packages embedded on a country page."""
    payload = parse_country_embedded_catalog(html, source_url)
    return set(payload.get("detail_urls") or [])


def _sid(source_url: str, heading: str, body: str) -> str:
    return hashlib.sha1(f"{source_url}|{heading}|{body[:80]}".encode()).hexdigest()[:16]


def make_section(
    source_url: str,
    heading: str,
    text: str,
    *,
    path: list[str] | None = None,
    min_chars: int = 20,
) -> Section | None:
    body = clean_text(text)
    if len(body) < min_chars:
        return None
    heading = clean_text(heading) or "Section"
    trail = path or [heading]
    return Section(
        section_id=_sid(source_url, heading, body),
        heading_path=trail,
        heading_text=heading,
        text=body,
    )


def _main_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("main") or soup.body or soup
    return clean_text(root.get_text("\n", strip=True))


def _title_from_html(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    if h1:
        title = clean_text(h1.get_text(" ", strip=True))
        if title:
            return title
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return clean_text(str(og["content"]))
    if soup.title:
        return clean_text(soup.title.get_text(strip=True))
    return fallback


def _split_marked_blocks(text: str) -> dict[str, str]:
    """Split page text on known country headings. Last marker wins if duplicated."""
    lowered = text
    positions: list[tuple[int, str, str]] = []
    for needle, label in _COUNTRY_MARKERS:
        idx = lowered.lower().find(needle)
        if idx >= 0:
            positions.append((idx, label, needle))
    positions.sort(key=lambda row: row[0])
    blocks: dict[str, str] = {}
    for i, (start, label, needle) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        body = text[start + len(needle) : end]
        blocks[label] = clean_text(body)
    return blocks


def extract_faq_pairs(block: str, source_url: str, *, heading_prefix: str = "faq") -> list[Section]:
    """One chunk per question/answer. Handles '01 Question? Answer' and Q/A lines."""
    sections: list[Section] = []
    text = clean_text(block)
    if not text:
        return sections

    numbered = re.split(r"(?:(?<=^)|(?<=\n)|(?<=\.\s)|(?<=\?\s))\s*(?=\d{1,2}\s+)", text)
    pieces = [p.strip() for p in numbered if p.strip()]
    if len(pieces) >= 2:
        for piece in pieces:
            piece = re.sub(r"^\d{1,2}\s+", "", piece).strip()
            if "?" not in piece:
                continue
            q, _, rest = piece.partition("?")
            question = clean_text(q) + "?"
            answer = clean_text(rest)
            if len(answer) < 8:
                continue
            sec = make_section(
                source_url,
                question,
                f"Q: {question}\nA: {answer}",
                path=[heading_prefix, question],
            )
            if sec:
                sections.append(sec)
        if sections:
            return sections

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.endswith("?") and len(line) > 12:
            answer_parts: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].endswith("?"):
                if re.match(r"^\d{1,2}$", lines[i]):
                    i += 1
                    continue
                answer_parts.append(lines[i])
                i += 1
            answer = clean_text(" ".join(answer_parts))
            if len(answer) >= 8:
                sec = make_section(
                    source_url,
                    line,
                    f"Q: {line}\nA: {answer}",
                    path=[heading_prefix, line],
                )
                if sec:
                    sections.append(sec)
            continue
        i += 1
    return sections


def _selling_point_sections(block: str, source_url: str) -> list[Section]:
    sections: list[Section] = []
    text = clean_text(block)
    parts = re.split(r"(?=(?:^|\n)\s*\d{1,2}\s+)", text)
    for part in parts:
        part = re.sub(r"^\d{1,2}\s+", "", part).strip()
        if len(part) < 40:
            continue
        preview = part[:80]
        sec = make_section(source_url, preview, part, path=["selling_points", preview])
        if sec:
            sections.append(sec)
    if not sections and len(text) >= 40:
        sec = make_section(source_url, "Key Selling Points", text, path=["selling_points"])
        if sec:
            sections.append(sec)
    return sections


def _insight_sections(block: str, source_url: str) -> list[Section]:
    sections: list[Section] = []
    text = clean_text(block)
    # Title-like lines followed by a short body (insights are usually 2ΓÇô4 cards).
    chunks = re.split(r"\n{2,}", text)
    if len(chunks) >= 2:
        for chunk in chunks:
            chunk = clean_text(chunk)
            if len(chunk) < 40:
                continue
            title = chunk.split("\n", 1)[0][:120]
            sec = make_section(source_url, title, chunk, path=["market_insights", title])
            if sec:
                sections.append(sec)
    if not sections and len(text) >= 40:
        sec = make_section(source_url, "Recent Market Insights", text, path=["market_insights"])
        if sec:
            sections.append(sec)
    return sections[:8]


def extract_country_overview_document(html: str, source_url: str) -> Document:
    source_url = canonicalize_url(source_url)
    title = _title_from_html(html, "Country overview")
    embedded = parse_country_embedded_catalog(html, source_url)
    sections: list[Section] = []

    for name, text in embedded.get("insights") or []:
        sec = make_section(source_url, name, text, path=["market_insights", name])
        if sec:
            sections.append(sec)

    for point in embedded.get("selling_points") or []:
        preview = point[:80]
        sec = make_section(source_url, preview, point, path=["selling_points", preview])
        if sec:
            sections.append(sec)

    if embedded.get("overview"):
        sec = make_section(
            source_url,
            "Country Overview",
            embedded["overview"],
            path=["overview"],
        )
        if sec:
            sections.append(sec)

    process_steps = embedded.get("process_steps") or []
    if process_steps:
        lines = [f"{i}. {q}: {a}" for i, (q, a) in enumerate(process_steps, start=1)]
        sec = make_section(
            source_url,
            "Application process",
            "\n".join(lines),
            path=["process_flow"],
        )
        if sec:
            sections.append(sec)
        for q, a in process_steps:
            sec = make_section(source_url, q, f"{q}\n{a}", path=["process_flow", q])
            if sec:
                sections.append(sec)

    for q, a in embedded.get("faqs") or []:
        sec = make_section(
            source_url,
            q,
            f"Q: {q}\nA: {a}",
            path=["faq", q],
        )
        if sec:
            sections.append(sec)

    # Top Funds / packages from Explore Funds (or Explore Packages) cards.
    for item in (embedded.get("funds") or []) + (embedded.get("packages") or []):
        kind = "fund" if item.get("kind") == "funds" else "package"
        price = item.get("starting_price") or 0
        currency = item.get("currency") or ""
        detail = ""
        for u in embedded.get("detail_urls") or []:
            if item["slug"] in u:
                detail = u
                break
        body = (
            f"{kind.title()}: {item['title']}\n"
            f"Starting from: {currency} {price:,}\n"
            f"Country: {item.get('authority_name') or embedded.get('country_slug')}\n"
            f"Slug: {item['slug']}\n"
        )
        if detail:
            body += f"Details: {detail}\n"
        body += (
            "This is a public starting-from price on the country page. "
            "Open the fund/package detail page for process flow, documents, and FAQ when available."
        )
        sec = make_section(
            source_url,
            item["title"],
            body,
            path=["top_funds" if kind == "fund" else "top_packages", item["title"]],
        )
        if sec:
            sections.append(sec)

    # Fallback to visible-text markers when RSC payload is thin.
    if len(sections) < 3:
        blocks = _split_marked_blocks(_main_text(html))
        if blocks.get("market_insights") and not embedded.get("insights"):
            sections.extend(_insight_sections(blocks["market_insights"], source_url))
        if blocks.get("selling_points") and not embedded.get("selling_points"):
            sections.extend(_selling_point_sections(blocks["selling_points"], source_url))
        if blocks.get("overview") and not embedded.get("overview"):
            sec = make_section(source_url, "Country Overview", blocks["overview"], path=["overview"])
            if sec:
                sections.append(sec)
        if blocks.get("process_flow") and not process_steps:
            sec = make_section(
                source_url, "Application process", blocks["process_flow"], path=["process_flow"]
            )
            if sec:
                sections.append(sec)
        if blocks.get("faq") and not embedded.get("faqs"):
            faqs = extract_faq_pairs(blocks["faq"], source_url)
            sections.extend(faqs)

    if not sections:
        body = _main_text(html)[:12000]
        fallback = make_section(source_url, "overview", body, path=["overview"])
        if fallback:
            sections.append(fallback)

    return Document(
        title=title,
        source_url=source_url,
        source_domain="arnifi.com",
        category_name="country_overview",
        sections=sections,
    )


def extract_country_overview_from_api(payload: dict[str, Any], source_url: str) -> Document:
    """
    Build a country_overview Document from prodapi /country-overview/:slug JSON.

    Section labels match the HTML extractor so retrieval filters stay stable.
    """
    source_url = canonicalize_url(source_url)
    title = clean_text(str(payload.get("title") or "Country overview")) or "Country overview"
    country_slug = clean_text(
        str(
            (payload.get("country") or {}).get("slug")
            if isinstance(payload.get("country"), dict)
            else ""
        )
        or payload.get("title")
        or ""
    )
    sections: list[Section] = []

    for insight in payload.get("marketInsights") or []:
        if not isinstance(insight, dict):
            continue
        name = clean_text(str(insight.get("title") or "Market insight"))
        text = clean_text(str(insight.get("text") or ""))
        if not text:
            continue
        sec = make_section(source_url, name, text, path=["market_insights", name])
        if sec:
            sections.append(sec)

    for point in payload.get("keySellingPoints") or []:
        if isinstance(point, dict):
            text = clean_text(str(point.get("value") or ""))
        else:
            text = clean_text(str(point))
        if not text:
            continue
        preview = text[:80]
        sec = make_section(source_url, preview, text, path=["selling_points", preview])
        if sec:
            sections.append(sec)

    overview_parts: list[str] = []
    sub = clean_text(str(payload.get("subHeader") or ""))
    detail = clean_text(str(payload.get("overviewDetail") or ""))
    if sub:
        overview_parts.append(sub)
    if detail:
        overview_parts.append(detail)
    if overview_parts:
        sec = make_section(
            source_url,
            "Country Overview",
            "\n\n".join(overview_parts),
            path=["overview"],
        )
        if sec:
            sections.append(sec)

    process_steps: list[tuple[str, str]] = []
    for step in payload.get("processStep") or []:
        if not isinstance(step, dict):
            continue
        q = clean_text(str(step.get("question") or ""))
        a = clean_text(str(step.get("answer") or ""))
        if q and a:
            process_steps.append((q, a))
    if process_steps:
        lines = [f"{i}. {q}: {a}" for i, (q, a) in enumerate(process_steps, start=1)]
        sec = make_section(
            source_url,
            "Application process",
            "\n".join(lines),
            path=["process_flow"],
        )
        if sec:
            sections.append(sec)
        for q, a in process_steps:
            sec = make_section(source_url, q, f"{q}\n{a}", path=["process_flow", q])
            if sec:
                sections.append(sec)

    for faq in payload.get("FAQs") or []:
        if not isinstance(faq, dict):
            continue
        q = clean_text(str(faq.get("question") or ""))
        a = clean_text(str(faq.get("answer") or ""))
        if not q or not a:
            continue
        sec = make_section(source_url, q, f"Q: {q}\nA: {a}", path=["faq", q])
        if sec:
            sections.append(sec)

    for fund in payload.get("funds") or []:
        if not isinstance(fund, dict):
            continue
        fund_title = clean_text(str(fund.get("title") or ""))
        fund_slug = clean_text(str(fund.get("slug") or ""))
        if not fund_title:
            continue
        price = fund.get("startingPrice") or fund.get("starting_price") or 0
        try:
            price_i = int(price)
        except (TypeError, ValueError):
            price_i = 0
        currency = clean_text(str(fund.get("currency") or ""))
        authority = clean_text(str(fund.get("authorityName") or country_slug))
        fund_id = fund.get("id")
        detail = ""
        if fund_slug and fund_id is not None:
            slug_country = re.sub(r"[^a-z0-9]+", "-", authority.lower()).strip("-") or "unknown"
            detail = canonicalize_url(
                f"https://arnifi.com/product-details/funds/{slug_country}/{fund_slug}/{fund_id}"
            )
        body = (
            f"Fund: {fund_title}\n"
            f"Starting from: {currency} {price_i:,}\n"
            f"Country: {authority}\n"
            f"Slug: {fund_slug}\n"
        )
        if detail:
            body += f"Details: {detail}\n"
        body += (
            "This is a public starting-from price on the country page. "
            "Open the fund/package detail page for process flow, documents, and FAQ when available."
        )
        sec = make_section(
            source_url,
            fund_title,
            body,
            path=["top_funds", fund_title],
        )
        if sec:
            sections.append(sec)

    for pkg in payload.get("productPages") or []:
        if not isinstance(pkg, dict):
            continue
        pkg_title = clean_text(str(pkg.get("productName") or pkg.get("title") or ""))
        pkg_slug = clean_text(str(pkg.get("slug") or ""))
        if not pkg_title:
            continue
        price = (
            pkg.get("startingArnifiPrice")
            or pkg.get("startingPrice")
            or pkg.get("starting_price")
            or 0
        )
        try:
            price_i = int(price)
        except (TypeError, ValueError):
            price_i = 0
        authority = ""
        auth = pkg.get("authority_name")
        if isinstance(auth, dict):
            authority = clean_text(str(auth.get("AuthorityName") or auth.get("name") or ""))
        else:
            authority = clean_text(str(auth or country_slug))
        pkg_id = pkg.get("id")
        detail = ""
        if pkg_slug and pkg_id is not None:
            slug_country = re.sub(r"[^a-z0-9]+", "-", (authority or country_slug).lower()).strip(
                "-"
            ) or "unknown"
            detail = canonicalize_url(
                f"https://arnifi.com/product-details/business-setup/{slug_country}/{pkg_slug}/{pkg_id}"
            )
        body = (
            f"Package: {pkg_title}\n"
            f"Starting from: {price_i:,}\n"
            f"Country: {authority or country_slug}\n"
            f"Slug: {pkg_slug}\n"
        )
        if detail:
            body += f"Details: {detail}\n"
        body += (
            "This is a public starting-from price on the country page. "
            "Open the fund/package detail page for process flow, documents, and FAQ when available."
        )
        sec = make_section(
            source_url,
            pkg_title,
            body,
            path=["top_packages", pkg_title],
        )
        if sec:
            sections.append(sec)

    # Cross-country comparison table from prodapi `compare`
    compare_rows = payload.get("compare") or []
    if isinstance(compare_rows, list) and compare_rows:
        table_lines: list[str] = [
            f"Country comparison for {title}. Values are from the Arnifi country overview."
        ]
        for peer in compare_rows:
            if not isinstance(peer, dict):
                continue
            peer_name = clean_text(str(peer.get("countryName") or peer.get("slug") or ""))
            if not peer_name:
                continue
            peer_lines: list[str] = [f"Comparison peer: {peer_name}"]
            for row in peer.get("comparison") or []:
                if not isinstance(row, dict):
                    continue
                key = clean_text(str(row.get("key") or ""))
                val = clean_text(str(row.get("value") or ""))
                if not key or not val:
                    continue
                line = f"{peer_name} ΓÇö {key}: {val}"
                table_lines.append(line)
                peer_lines.append(f"{key}: {val}")
                sec = make_section(
                    source_url,
                    f"{peer_name} {key}",
                    line,
                    path=["compare", peer_name, key],
                )
                if sec:
                    sections.append(sec)
            if len(peer_lines) > 1:
                sec = make_section(
                    source_url,
                    peer_name,
                    "\n".join(peer_lines),
                    path=["compare", peer_name],
                )
                if sec:
                    sections.append(sec)
        if len(table_lines) > 1:
            sec = make_section(
                source_url,
                "Country comparison",
                "\n".join(table_lines),
                path=["compare"],
            )
            if sec:
                sections.append(sec)

    if not sections:
        fallback_text = detail or sub or title
        fallback = make_section(source_url, "overview", fallback_text, path=["overview"])
        if fallback:
            sections.append(fallback)

    return Document(
        title=title,
        source_url=source_url,
        source_domain="arnifi.com",
        category_name="country_overview",
        sections=sections,
    )


def _documents_required_section(
    items: Any,
    source_url: str,
    *,
    heading: str = "Documents Required",
    path_label: str = "documents_required",
) -> Section | None:
    """
    Build one numbered Documents Required section from CMS list payloads.

    Items often only have a ``name`` (description null). Short names must not be
    dropped by make_section's min-length filter ΓÇö keep them as a numbered list.
    """
    blocks = _iter_named_blocks(items)
    if not blocks:
        return None
    lines: list[str] = []
    for i, (name, body) in enumerate(blocks, start=1):
        label = name or body
        if body and body != label and label not in body:
            lines.append(f"{i}. {label}: {body}")
        else:
            lines.append(f"{i}. {label}")
    return make_section(
        source_url,
        heading,
        "\n".join(lines),
        path=[path_label],
        min_chars=12,
    )


def _iter_named_blocks(items: Any) -> list[tuple[str, str]]:
    """Normalize highlights / process / FAQ / T&C list items to (heading, body)."""
    out: list[tuple[str, str]] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if isinstance(item, str):
            text = clean_text(item)
            if text:
                out.append((text[:80], text))
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
        if not body and heading:
            body = heading
        if not body:
            continue
        if not heading:
            heading = body[:80]
        out.append((heading, body))
    return out


def extract_micro_service_from_api(payload: dict[str, Any], source_url: str) -> Document:
    """
    Build a product_detail Document from prodapi /micro-services/:type/:slug JSON.

    Prompt sections (plus optional pricing):
    introduction, documents_required, highlights_and_benefits, process_flow,
    faqs (per Q&A), terms_and_conditions.

    Important: CMS field ``documentsRequired`` is the website "Documents required"
    list. Do not confuse with FAQs that mention documents, or with the often-empty
    ``documentsFromAuthority`` array.
    """
    source_url = canonicalize_url(source_url)
    title = clean_text(str(payload.get("title") or payload.get("slug") or "Service"))
    service_type = clean_text(str(payload.get("type") or ""))
    sections: list[Section] = []

    intro = clean_text(
        str(payload.get("description") or payload.get("shortDescription") or "")
    )
    if intro:
        sec = make_section(
            source_url,
            "Introduction",
            intro,
            path=["introduction"],
        )
        if sec:
            sections.append(sec)

    docs_sec = _documents_required_section(payload.get("documentsRequired"), source_url)
    if docs_sec:
        sections.append(docs_sec)

    highlight_blocks = _iter_named_blocks(payload.get("highlightsAndBenifits"))
    if highlight_blocks:
        body = "\n\n".join(
            f"{h}\n{b}" if h not in b else b for h, b in highlight_blocks
        )
        sec = make_section(
            source_url,
            "Highlights & Benefits",
            body,
            path=["highlights_and_benefits"],
        )
        if sec:
            sections.append(sec)

    process_blocks = _iter_named_blocks(payload.get("processFlow"))
    if process_blocks:
        lines = [f"{i}. {h}: {b}" for i, (h, b) in enumerate(process_blocks, start=1)]
        sec = make_section(
            source_url,
            "Process Flow",
            "\n".join(lines),
            path=["process_flow"],
        )
        if sec:
            sections.append(sec)

    for heading, body in _iter_named_blocks(payload.get("faqs")):
        sec = make_section(
            source_url,
            heading,
            f"Q: {heading}\nA: {body}",
            path=["faqs", heading],
        )
        if sec:
            sections.append(sec)

    term_blocks = _iter_named_blocks(payload.get("termsAndConditions"))
    if term_blocks:
        body = "\n\n".join(b for _, b in term_blocks)
        sec = make_section(
            source_url,
            "Terms & Conditions",
            body,
            path=["terms_and_conditions"],
        )
        if sec:
            sections.append(sec)

    for heading, body in _iter_named_blocks(payload.get("documentsFromAuthority")):
        sec = make_section(
            source_url,
            heading,
            body,
            path=["documents_from_authority", heading],
        )
        if sec:
            sections.append(sec)

    more_docs = _documents_required_section(
        payload.get("moreAboutRequiredDocuments"),
        source_url,
        heading="More about required documents",
        path_label="more_about_required_documents",
    )
    if more_docs:
        sections.append(more_docs)

    price = payload.get("price") or payload.get("startingPrice") or payload.get("currentPrice")
    currency = clean_text(str(payload.get("currency") or ""))
    eta = clean_text(str(payload.get("estimatedDeliveryTime") or ""))
    price_bits = [f"Service: {title}"]
    if service_type:
        price_bits.append(f"Type: {service_type}")
    if price not in (None, "", 0, "0"):
        price_bits.append(f"Starting from: {currency} {price}".strip())
    if eta:
        price_bits.append(f"Estimated delivery: {eta}")
    if len(price_bits) > 1:
        sec = make_section(
            source_url,
            "Pricing",
            "\n".join(price_bits),
            path=["pricing"],
        )
        if sec:
            sections.append(sec)

    if not sections:
        fallback = make_section(source_url, title, title, path=["introduction"])
        if fallback:
            sections.append(fallback)

    return Document(
        title=title,
        source_url=source_url,
        source_domain="arnifi.com",
        category_name=service_type or "micro_service",
        sections=sections,
    )


def extract_setup_product_from_api(payload: dict[str, Any], source_url: str) -> Document:
    """
    Build a product_detail Document from Strapi /product-pages attributes.

    Setup licences often only expose description + pricing (FAQs may be empty).
    """
    source_url = canonicalize_url(source_url)
    attrs = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else payload
    title = clean_text(str(attrs.get("productName") or attrs.get("title") or "Package"))
    sections: list[Section] = []

    intro = clean_text(str(attrs.get("description") or ""))
    if intro:
        sec = make_section(source_url, "Introduction", intro, path=["introduction"])
        if sec:
            sections.append(sec)

    for heading, body in _iter_named_blocks(attrs.get("faqs")):
        sec = make_section(
            source_url,
            heading,
            f"Q: {heading}\nA: {body}",
            path=["faq", heading],
        )
        if sec:
            sections.append(sec)

    price = attrs.get("startingArnifiPrice") or attrs.get("startingOfficialPrice")
    currency = clean_text(str(attrs.get("currency") or ""))
    bits = [f"Package: {title}"]
    if price not in (None, "", 0, "0"):
        bits.append(f"Starting from: {currency} {price}".strip())
    plan = attrs.get("pricingPlan")
    if isinstance(plan, dict):
        pricing = plan.get("pricing")
        if isinstance(pricing, list) and pricing:
            bits.append(f"Pricing plan: {json.dumps(pricing, ensure_ascii=False)[:800]}")
    if len(bits) > 1 or intro:
        sec = make_section(source_url, "Pricing", "\n".join(bits), path=["pricing"])
        if sec:
            sections.append(sec)

    if not sections:
        fallback = make_section(source_url, title, title, path=["introduction"])
        if fallback:
            sections.append(fallback)

    return Document(
        title=title,
        source_url=source_url,
        source_domain="arnifi.com",
        category_name=clean_text(str(attrs.get("productType") or "licence")),
        sections=sections,
    )


def _classify_heading(text: str) -> str | None:
    lowered = text.lower()
    for label, aliases in PACKAGE_SECTION_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return label
    return None


def _heading_blocks(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("main") or soup.body or soup
    headings = root.find_all(["h1", "h2", "h3", "h4"]) if isinstance(root, Tag) else []
    blocks: list[tuple[str, str]] = []
    for i, heading in enumerate(headings):
        title = clean_text(heading.get_text(" ", strip=True))
        if len(title) < 3:
            continue
        bits: list[str] = []
        for sib in heading.next_siblings:
            if isinstance(sib, Tag) and sib.name in {"h1", "h2", "h3", "h4"}:
                break
            if isinstance(sib, Tag):
                bits.append(sib.get_text(" ", strip=True))
        if not bits:
            parent = heading.parent
            if isinstance(parent, Tag):
                bits.append(parent.get_text(" ", strip=True))
        body = clean_text(" ".join(bits))
        if title and body:
            blocks.append((title, body))
    return blocks


def extract_package_detail_document(
    html: str,
    source_url: str,
    page_kind: str,
) -> tuple[Document, list[str]]:
    source_url = canonicalize_url(source_url)
    title = _title_from_html(html, "Package")
    sections: list[Section] = []
    seen_labels: set[str] = set()

    for heading, body in _heading_blocks(html):
        label = _classify_heading(heading) or "introduction"
        if label == "faq":
            faqs = extract_faq_pairs(body, source_url, heading_prefix="faq")
            if faqs:
                sections.extend(faqs)
                seen_labels.add("faq")
                continue
        if label == "documents_required":
            for piece in re.split(r"\n+|ΓÇó|;", body):
                piece = clean_text(piece)
                if len(piece) < 12:
                    continue
                sec = make_section(
                    source_url,
                    piece[:80],
                    piece,
                    path=["documents_required", piece[:80]],
                )
                if sec:
                    sections.append(sec)
            seen_labels.add("documents_required")
            continue
        sec = make_section(source_url, heading, body, path=[label, heading])
        if sec:
            sections.append(sec)
            seen_labels.add(label)

    if not sections:
        body = _main_text(html)[:12000]
        fallback = make_section(source_url, "introduction", body, path=["introduction"])
        if fallback:
            sections.append(fallback)

    missing = [name for name in EXPECTED_PACKAGE_SECTIONS if name not in seen_labels]
    return (
        Document(
            title=title,
            source_url=source_url,
            source_domain="arnifi.com",
            category_name=page_kind,
            sections=sections,
        ),
        missing,
    )


def missing_package_sections(document: Document) -> list[str]:
    present = { (s.heading_path[0] if s.heading_path else "").lower() for s in document.sections }
    return [name for name in EXPECTED_PACKAGE_SECTIONS if name not in present]
