"""Press Releases / Events / Case Studies discovery + detail extract.

Press + Events: HTML listing (prodapi /events is 403). Detail payloads are
embedded in Next.js flight HTML; press body text is SSR'd in the page.
Case Studies: prodapi /case-studies preferred; industry filter pages are
discovery-only for URL union checks (dedupe by detail URL).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.models.schemas import Document, Section
from app.services.ingestion.website_sections import make_section
from app.services.prodapi.client import ProdapiClient
from app.utils.helpers import canonicalize_url, clean_text, get_logger

logger = get_logger(__name__)

PRESS_LISTING_URL = "https://arnifi.com/announcement/press-releases"
EVENTS_LISTING_URL = "https://arnifi.com/announcement/events"
CASE_STUDIES_LISTING_URL = "https://arnifi.com/case-studies"

HTTP_HEADERS = {
    "Accept": "text/html",
    "User-Agent": "ArnifiKnowledgeBot/1.0",
}

TRUNCATION_RE = re.compile(
    r"\b(Read More|View more|View More|Show more|Load more)\b\.?",
    flags=re.IGNORECASE,
)

_STOP_BODY_LINES = frozenset(
    {
        "view article",
        "notify me",
        "related articles",
        "related events",
        "you may also like",
        "share this",
        "back to all",
        "upcoming events",
        "archived events",
    }
)


def strip_truncation_artifacts(text: str) -> str:
    """Remove listing truncation crumbs left in scraped body copy."""
    cleaned = TRUNCATION_RE.sub("", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _fetch_html(url: str, *, timeout: int = 40) -> str:
    response = requests.get(url, timeout=timeout, headers=HTTP_HEADERS)
    response.raise_for_status()
    return response.text


def _path(url: str) -> str:
    return urlparse(canonicalize_url(url)).path.rstrip("/") or "/"


def _detail_links_from_listing(
    html: str,
    listing_url: str,
    *,
    path_prefix: str,
) -> list[str]:
    """Collect unique detail URLs under path_prefix from listing anchors."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    seen: set[str] = set()
    listing_path = _path(listing_url)
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if not href or path_prefix not in href:
            continue
        absolute = canonicalize_url(urljoin(listing_url, href))
        path = _path(absolute)
        if path == listing_path or path == path_prefix.rstrip("/"):
            continue
        # Skip industry / filter subpaths accidentally matching
        if "/industries/" in path:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
    return found


def _follow_listing_pages(
    seed_url: str,
    *,
    path_prefix: str,
    max_pages: int = 30,
) -> tuple[list[str], dict[str, Any]]:
    """
    Crawl listing pages via ?page=N until no new detail URLs appear.

    Returns (detail_urls, pagination_notes).
    """
    notes: dict[str, Any] = {
        "seed": seed_url,
        "pagination": "none_detected",
        "pages_fetched": 0,
        "has_load_more_text": False,
    }
    all_urls: list[str] = []
    seen: set[str] = set()
    page = 1
    while page <= max_pages:
        url = seed_url if page == 1 else f"{seed_url.rstrip('/')}?page={page}"
        try:
            html = _fetch_html(url)
        except Exception as exc:
            if page == 1:
                raise
            logger.warning("Listing page fetch failed %s: %s", url, exc)
            notes["stopped_on"] = url
            notes["stop_reason"] = str(exc)
            break
        notes["pages_fetched"] += 1
        plain = re.sub(r"<[^>]+>", " ", html)
        if re.search(r"load\s*more", plain, flags=re.I):
            notes["has_load_more_text"] = True
        batch = _detail_links_from_listing(html, seed_url, path_prefix=path_prefix)
        new = [u for u in batch if u not in seen]
        if not new:
            if page == 1:
                notes["pagination"] = "single_page"
            break
        for u in new:
            seen.add(u)
            all_urls.append(u)
        if page == 1 and ("?page=" in html or "page=" in html):
            notes["pagination"] = "query_page"
        elif page > 1:
            notes["pagination"] = "query_page"
        else:
            notes["pagination"] = "single_page"
            # No page links and no new pages expected
            if "?page=" not in html and not notes["has_load_more_text"]:
                break
        page += 1
        # If first page had no page= hints, do not keep incrementing blindly
        if notes["pagination"] == "single_page":
            break
    notes["confirmed_total"] = len(all_urls)
    return all_urls, notes


def _unescape_flight_for_json(text: str) -> str:
    """Unescape Next flight quotes without turning \\n into raw newlines (breaks JSON)."""
    return text.replace("\\u0026", "&").replace('\\"', '"')


def _extract_embedded_announcement(html: str, *, slug: str) -> dict[str, Any] | None:
    """Parse Press Release / Event CMS object from Next flight HTML for slug."""
    norm = _unescape_flight_for_json(html)
    needle = f'"slug":"{slug}"'
    idx = 0
    while True:
        pos = norm.find(needle, idx)
        if pos < 0:
            return None
        # Prefer object that also carries announcement "type"
        window_start = max(0, pos - 8000)
        type_pos = norm.rfind('"type":', window_start, pos)
        start = norm.rfind("{", window_start, type_pos + 1) if type_pos >= 0 else -1
        if start < 0:
            start = norm.rfind("{", 0, pos)
        if start < 0:
            idx = pos + 1
            continue
        depth = 0
        end = None
        in_string = False
        escape = False
        for i in range(start, min(len(norm), start + 120000)):
            ch = norm[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            idx = pos + 1
            continue
        blob = norm[start:end]
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            idx = pos + 1
            continue
        if (
            isinstance(obj, dict)
            and str(obj.get("slug") or "") == slug
            and obj.get("type")
        ):
            return obj
        idx = pos + 1
    return None


def _slug_from_detail_url(url: str) -> str:
    parts = [p for p in _path(url).split("/") if p]
    return parts[-1] if parts else ""


def _extract_visible_body(html: str, *, title: str) -> str:
    """Pull article/event body paragraphs from SSR HTML (nav stripped)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n", strip=True).split("\n") if ln.strip()]
    title_norm = clean_text(title).lower()
    start = None
    for i, ln in enumerate(lines):
        if title_norm and (
            title_norm in ln.lower() or ln.lower() in title_norm
        ):
            start = i + 1
            break
    if start is None:
        # Fallback: longest consecutive paragraph run
        paras = [ln for ln in lines if len(ln) >= 80]
        return strip_truncation_artifacts("\n\n".join(paras[:12]))

    body: list[str] = []
    for ln in lines[start:]:
        low = ln.lower().strip()
        if low in _STOP_BODY_LINES or any(low.startswith(s) for s in _STOP_BODY_LINES):
            break
        # Skip short chrome / nav crumbs once we have substance
        if len(ln) < 40 and body and len(body) >= 1:
            # Allow short date / source lines early
            if body and len(body[0]) < 60 and len(body) <= 2:
                body.append(ln)
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}", ln) or re.match(
                r"^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}", ln
            ):
                body.append(ln)
                continue
            continue
        if len(ln) < 20 and not body:
            continue
        body.append(ln)
        if len(body) >= 40:
            break
    return strip_truncation_artifacts("\n\n".join(body))


def _paragraph_group_sections(
    source_url: str,
    body: str,
    *,
    group_size: int = 2,
) -> list[Section]:
    """Split full body into paragraph-group sections for chunking."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paras:
        text = clean_text(body)
        if not text:
            return []
        return [
            make_section(
                source_url,
                "Body",
                text,
                path=["body"],
                min_chars=12,
            )
        ]
    sections: list[Section] = []
    for i in range(0, len(paras), group_size):
        group = paras[i : i + group_size]
        text = strip_truncation_artifacts("\n\n".join(group))
        if not text:
            continue
        idx = (i // group_size) + 1
        sec = make_section(
            source_url,
            f"Part {idx}",
            text,
            path=["body", f"part-{idx}"],
            min_chars=12,
        )
        if sec:
            sections.append(sec)
    return sections


def extract_press_release(html: str, source_url: str) -> tuple[Document, dict[str, Any]]:
    """Extract press release detail ΓåÆ Document + metadata dict."""
    source_url = canonicalize_url(source_url)
    slug = _slug_from_detail_url(source_url)
    embedded = _extract_embedded_announcement(html, slug=slug) or {}
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = clean_text(str(embedded.get("title") or ""))
    if not title and h1:
        title = clean_text(h1.get_text(strip=True))
    if not title:
        title = slug.replace("-", " ") or "Press Release"
    publish_date = (
        clean_text(str(embedded.get("date") or embedded.get("publishedAt") or ""))
        or None
    )
    source_publication = clean_text(str(embedded.get("location") or "")) or None
    # CMS description for press is often a placeholder; prefer SSR body.
    cms_desc = clean_text(str(embedded.get("description") or ""))
    body = _extract_visible_body(html, title=title)
    if (not body or len(body) < 80) and cms_desc and len(cms_desc) > 40 and not cms_desc.startswith("$"):
        body = strip_truncation_artifacts(cms_desc)
    if not body:
        raise RuntimeError(f"No press body extracted for {source_url}")

    sections = _paragraph_group_sections(source_url, body)
    if not sections:
        raise RuntimeError(f"No press sections for {source_url}")

    doc = Document(
        title=title,
        published_at=publish_date,
        source_url=source_url,
        category_name="Press Releases",
        category_url=PRESS_LISTING_URL,
        sections=sections,
    )
    meta = {
        "content_type": "press_release",
        "title": title,
        "publish_date": publish_date,
        "source_publication": source_publication,
        "full_body_text": body,
        "url": source_url,
        "article_link": embedded.get("articleLink"),
        "cms_type": embedded.get("type"),
    }
    return doc, meta


def extract_event(html: str, source_url: str) -> tuple[Document, dict[str, Any]]:
    """Extract archived event detail; raises if Notify Me / upcoming form."""
    source_url = canonicalize_url(source_url)
    slug = _slug_from_detail_url(source_url)
    embedded = _extract_embedded_announcement(html, slug=slug) or {}
    cms_type = str(embedded.get("type") or "")
    is_upcoming = bool(embedded.get("isUpcomingEvents"))
    plain = re.sub(r"<[^>]+>", " ", html)
    if is_upcoming or "upcoming" in cms_type.lower():
        raise RuntimeError(f"Skipping upcoming/Notify Me event: {source_url}")
    # Only treat as Notify Me form when there is no archived body to ingest.
    body_probe = clean_text(str(embedded.get("description") or ""))
    if (
        not body_probe
        and "Notify Me" in plain
        and (is_upcoming or "upcoming" in cms_type.lower() or not cms_type)
    ):
        raise RuntimeError(f"Skipping Notify Me form page: {source_url}")

    title = clean_text(str(embedded.get("title") or "")) or slug.replace("-", " ")
    event_date = clean_text(str(embedded.get("date") or "")) or None
    start = embedded.get("startTime")
    end = embedded.get("endTime")
    if start and end:
        event_time = f"{start} ΓÇô {end}"
    elif start:
        event_time = str(start)
    else:
        event_time = None
    location = clean_text(str(embedded.get("location") or "")) or None
    partners_raw = embedded.get("partners")
    partners: list[str] = []
    if isinstance(partners_raw, list):
        for p in partners_raw:
            if isinstance(p, str) and p.strip():
                partners.append(clean_text(p))
            elif isinstance(p, dict):
                name = p.get("name") or p.get("title")
                if name:
                    partners.append(clean_text(str(name)))

    body = strip_truncation_artifacts(
        clean_text(str(embedded.get("description") or ""))
    )
    if not body or len(body) < 40:
        body = _extract_visible_body(html, title=title)
    if not body:
        raise RuntimeError(f"No event body extracted for {source_url}")

    sections = _paragraph_group_sections(source_url, body)
    if not sections:
        raise RuntimeError(f"No event sections for {source_url}")

    doc = Document(
        title=title,
        published_at=event_date,
        source_url=source_url,
        category_name="Events",
        category_url=EVENTS_LISTING_URL,
        sections=sections,
    )
    meta = {
        "content_type": "event",
        "title": title,
        "event_date": event_date,
        "event_time": event_time,
        "location": location,
        "partners": partners,
        "full_body_text": body,
        "url": source_url,
        "cms_type": cms_type,
        "notify_me_skipped": False,
    }
    return doc, meta


def _plain_from_children(node: Any) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    parts: list[str] = []
    text = node.get("text")
    if isinstance(text, str):
        parts.append(text)
    for child in node.get("children") or []:
        parts.append(_plain_from_children(child))
    return "".join(parts)


def _case_blocks_to_sections(
    source_url: str, blocks: list[Any]
) -> tuple[list[Section], str]:
    """
    Convert Strapi-like rich text blocks to sections.

    Returns (sections, mode) where mode is 'section' or 'paragraph'.
    """
    headed: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_paras: list[str] = []
    bare_paras: list[str] = []

    def flush() -> None:
        nonlocal current_heading, current_paras
        if current_heading is None:
            return
        text = strip_truncation_artifacts("\n\n".join(p for p in current_paras if p))
        if text:
            headed.append((current_heading, [text]))
        current_heading = None
        current_paras = []

    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "")
        if btype == "heading":
            flush()
            current_heading = clean_text(_plain_from_children(block)) or "Section"
            current_paras = []
        elif btype in {"paragraph", "quote", "list", "list-item"}:
            text = clean_text(_plain_from_children(block))
            if not text:
                continue
            if current_heading is not None:
                current_paras.append(text)
            else:
                bare_paras.append(text)
        else:
            text = clean_text(_plain_from_children(block))
            if text and current_heading is not None:
                current_paras.append(text)
            elif text:
                bare_paras.append(text)
    flush()

    sections: list[Section] = []
    if headed:
        for i, (heading, texts) in enumerate(headed, start=1):
            body = strip_truncation_artifacts("\n\n".join(texts))
            sec = make_section(
                source_url,
                heading,
                body,
                path=[re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or f"s{i}"],
                min_chars=12,
            )
            if sec:
                sections.append(sec)
        if bare_paras:
            intro = strip_truncation_artifacts("\n\n".join(bare_paras))
            sec = make_section(
                source_url, "Overview", intro, path=["overview"], min_chars=12
            )
            if sec:
                sections.insert(0, sec)
        return sections, "section"

    body = strip_truncation_artifacts("\n\n".join(bare_paras))
    return _paragraph_group_sections(source_url, body), "paragraph"


def _relation_name(value: Any) -> str | None:
    if isinstance(value, dict):
        name = value.get("name") or value.get("title")
        if name:
            return clean_text(str(name))
    if isinstance(value, str) and value.strip():
        return clean_text(value)
    return None


def extract_case_study_from_api(
    payload: dict[str, Any], source_url: str
) -> tuple[Document, dict[str, Any]]:
    """Build Document from prodapi case-study detail JSON."""
    source_url = canonicalize_url(source_url)
    title = clean_text(str(payload.get("title") or payload.get("slug") or "Case Study"))
    publish_date = clean_text(str(payload.get("publishedAt") or "")) or None
    read_time = clean_text(str(payload.get("readingTime") or "")) or None
    jurisdiction = _relation_name(payload.get("jurisdiction"))
    industry = _relation_name(payload.get("industry"))
    description = payload.get("description")
    sections: list[Section] = []
    mode = "paragraph"
    if isinstance(description, list):
        sections, mode = _case_blocks_to_sections(source_url, description)
    elif isinstance(description, str) and description.strip():
        sections = _paragraph_group_sections(
            source_url, strip_truncation_artifacts(clean_text(description))
        )
        mode = "paragraph"
    if not sections:
        raise RuntimeError(f"No case study sections for {source_url}")

    doc = Document(
        title=title,
        published_at=publish_date,
        source_url=source_url,
        category_name="Case Studies",
        category_url=CASE_STUDIES_LISTING_URL,
        sections=sections,
    )
    meta = {
        "content_type": "case_study",
        "title": title,
        "jurisdiction": jurisdiction,
        "industry": industry,
        "read_time": read_time,
        "publish_date": publish_date,
        "sections": [
            {"heading": s.heading_text, "text": s.text} for s in sections
        ],
        "url": source_url,
        "chunk_mode": mode,
    }
    return doc, meta


def discover_press_releases() -> dict[str, Any]:
    urls, notes = _follow_listing_pages(
        PRESS_LISTING_URL, path_prefix="/announcement/press-releases/"
    )
    # Probe prodapi (expected 404)
    api_status = None
    try:
        r = requests.get(
            "https://prodapi.arnifi.com/api/press-releases",
            timeout=20,
            headers={"Accept": "application/json", "Origin": "https://arnifi.com"},
        )
        api_status = r.status_code
    except Exception as exc:
        api_status = f"error:{exc}"
    return {
        "content_type": "press_release",
        "listing_url": PRESS_LISTING_URL,
        "discovery": "html" if api_status != 200 else "prodapi",
        "prodapi_status": api_status,
        "confirmed_total": len(urls),
        "complete": len(urls) > 0,
        "detail_urls": urls,
        "pagination": notes,
        "ambiguity": None if urls else "no_detail_urls_found",
    }


def discover_events(*, archived_only: bool = True) -> dict[str, Any]:
    urls, notes = _follow_listing_pages(
        EVENTS_LISTING_URL, path_prefix="/announcement/events/"
    )
    api_status = None
    try:
        r = requests.get(
            "https://prodapi.arnifi.com/api/events",
            timeout=20,
            headers={
                "Accept": "application/json",
                "Origin": "https://arnifi.com",
                "Referer": "https://arnifi.com/",
            },
        )
        api_status = r.status_code
    except Exception as exc:
        api_status = f"error:{exc}"

    kept: list[str] = []
    skipped_notify: list[str] = []
    if archived_only:
        for url in urls:
            try:
                html = _fetch_html(url)
            except Exception as exc:
                logger.warning("Event probe failed %s: %s", url, exc)
                skipped_notify.append(url)
                continue
            slug = _slug_from_detail_url(url)
            embedded = _extract_embedded_announcement(html, slug=slug) or {}
            if embedded.get("isUpcomingEvents") or "upcoming" in str(
                embedded.get("type") or ""
            ).lower():
                skipped_notify.append(url)
                continue
            kept.append(url)
    else:
        kept = list(urls)

    return {
        "content_type": "event",
        "listing_url": EVENTS_LISTING_URL,
        "discovery": "html",
        "prodapi_status": api_status,
        "listing_total": len(urls),
        "confirmed_total": len(kept),
        "notify_me_skipped": len(skipped_notify),
        "skipped_urls": skipped_notify,
        "complete": len(kept) > 0,
        "detail_urls": kept,
        "pagination": notes,
        "ambiguity": None if kept else "no_archived_events_found",
    }


def discover_case_studies(
    client: ProdapiClient | None = None,
) -> dict[str, Any]:
    client = client or ProdapiClient()
    api_rows: list[dict[str, Any]] = []
    discovery = "html"
    try:
        api_rows = client.list_case_studies()
        discovery = "prodapi"
    except Exception as exc:
        logger.warning("case-studies API list failed: %s", exc)

    api_urls: list[str] = []
    items: list[dict[str, Any]] = []
    for row in api_rows:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        url = canonicalize_url(f"https://arnifi.com/case-studies/{slug}")
        api_urls.append(url)
        items.append(
            {
                "slug": slug,
                "title": row.get("title"),
                "detail_url": url,
                "industry": _relation_name(row.get("industry")),
                "jurisdiction": _relation_name(row.get("jurisdiction")),
            }
        )

    # HTML main listing
    main_html = _fetch_html(CASE_STUDIES_LISTING_URL)
    main_urls, main_notes = _follow_listing_pages(
        CASE_STUDIES_LISTING_URL, path_prefix="/case-studies/"
    )
    # Filter out industry paths accidentally
    main_urls = [u for u in main_urls if "/industries/" not in u]

    # Industry filter pages ΓÇö discovery union only
    soup = BeautifulSoup(main_html, "lxml")
    industry_hubs = sorted(
        {
            canonicalize_url(urljoin(CASE_STUDIES_LISTING_URL, a["href"]))
            for a in soup.find_all("a", href=True)
            if "/case-studies/industries/" in a["href"]
        }
    )
    industry_urls: set[str] = set()
    for hub in industry_hubs:
        try:
            ih = _fetch_html(hub)
        except Exception as exc:
            logger.warning("Industry listing failed %s: %s", hub, exc)
            continue
        for u in _detail_links_from_listing(
            ih, CASE_STUDIES_LISTING_URL, path_prefix="/case-studies/"
        ):
            if "/industries/" not in u:
                industry_urls.add(u)

    main_set = set(main_urls)
    api_set = set(api_urls)
    union = sorted(main_set | api_set | industry_urls)
    only_industry = sorted(industry_urls - main_set - api_set)
    # Prefer API order when available; else main listing
    detail_urls = api_urls if api_urls else main_urls
    # Ensure union extras are included once (should be empty in practice)
    for u in union:
        if u not in detail_urls:
            detail_urls.append(u)

    confirmed = len(detail_urls)
    live_match = (
        confirmed == len(main_set) == len(api_set)
        if api_set and main_set
        else confirmed > 0
    )
    ambiguity = None
    if api_set and main_set and api_set != main_set:
        ambiguity = "api_and_html_url_sets_differ"
    if only_industry:
        ambiguity = (ambiguity or "") + ";industry_only_urls=" + str(len(only_industry))

    return {
        "content_type": "case_study",
        "listing_url": CASE_STUDIES_LISTING_URL,
        "discovery": discovery,
        "confirmed_total": confirmed,
        "main_listing_total": len(main_set),
        "api_total": len(api_set),
        "industry_union_total": len(industry_urls),
        "industry_only_urls": only_industry,
        "dedupe_dropped": max(0, len(main_set) + len(industry_urls) - len(union)),
        "complete": live_match and not only_industry,
        "detail_urls": detail_urls,
        "items": items,
        "pagination": main_notes,
        "industry_hubs": industry_hubs,
        "ambiguity": ambiguity,
    }


def inventory_all(
    client: ProdapiClient | None = None,
) -> dict[str, Any]:
    """Operator inventory for press / events / case studies."""
    client = client or ProdapiClient()
    press = discover_press_releases()
    events = discover_events(archived_only=True)
    cases = discover_case_studies(client)
    return {
        "press_releases": press,
        "events": events,
        "case_studies": cases,
        "ambiguities": [
            {"stream": k, "detail": v.get("ambiguity")}
            for k, v in (
                ("press_releases", press),
                ("events", events),
                ("case_studies", cases),
            )
            if v.get("ambiguity")
        ],
    }
