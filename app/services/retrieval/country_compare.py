"""Deterministic multi-country comparison answers from prodapi `compare` tables.

Vector retrieval often ranks per-field peer chips (BVI Setup Timeline, ΓÇª) above the
full Cayman compare table, so the LLM sees incomplete context and answers
\"Not provided\". The website table is already complete in each country overview's
`compare` array ΓÇö use that directly when the user asks to compare 2+ countries.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.schemas import RetrievedChunk
from app.services.prodapi.client import ProdapiClient, country_page_url
from app.services.retrieval.catalog_focus import mentioned_country_tokens
from app.services.retrieval.location_catalog import load_location_catalog
from app.utils.helpers import clean_text, get_logger

logger = get_logger(__name__)


def _html_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_COMPARE_QUERY = re.compile(
    r"(?is)\b("
    r"compar(?:e|ison|ing)|"
    r"\bvs\.?\b|"
    r"versus|"
    r"difference(?:s)?\s+between|"
    r"how\s+does\s+.+\s+differ"
    r")\b"
)

_PREFERRED_KEYS = (
    "Setup Timeline",
    "Capital Requirement",
    "Corporate Tax Rate",
    "VAT Rate",
    "Virtual Company Setup",
    "Foreign Ownership",
    "Visa Application",
    "International Market Access",
    "Tax Incentives",
    "Banking",
    "Annual Compliance",
    "Preferred Business Activities",
)

# Question tokens / shortcodes ΓåÆ lookup key (resolved to exact API slug at runtime).
# Prodapi slugs are case-sensitive (Ireland, Hong-Kong, United Kingdom, ΓÇª).
_TOKEN_TO_SHORTCODE: dict[str, str] = {
    "cym": "cym",
    "cayman": "cym",
    "vg": "vg",
    "virgin": "vg",
    "bvi": "vg",
    "ae": "ae",
    "uae": "ae",
    "sa": "sa",
    "saudi": "sa",
    "sg": "sg",
    "singapore": "sg",
    "hk": "hk",
    "hong kong": "hk",
    "hong-kong": "hk",
    "gg": "gg",
    "guernsey": "gg",
    "mu": "mu",
    "mauritius": "mu",
    "cy": "cy",
    "cyprus": "cy",
    "ie": "ie",
    "ireland": "ie",
    "lu": "lu",
    "luxembourg": "lu",
    "my": "my",
    "malaysia": "my",
    "gb": "gb",
    "uk": "gb",
    "united kingdom": "gb",
    "united-kingdom": "gb",
    "pr": "pr",
    "puerto": "pr",
    "vc": "vc",
    "vincent": "vc",
    "grenadines": "vc",
}


def _api_slug_by_shortcode(client: ProdapiClient | None = None) -> dict[str, str]:
    """shortcode(lower) ΓåÆ exact prodapi country-overview slug."""
    catalog = load_location_catalog()
    out: dict[str, str] = {}
    for info in catalog.values():
        if not isinstance(info, dict):
            continue
        sc = str(info.get("shortcode") or "").strip().lower()
        slug = str(info.get("api_slug") or info.get("slug") or "").strip()
        if sc and slug:
            out[sc] = slug
    if len(out) >= 10:
        return out
    client = client or ProdapiClient()
    try:
        for row in client.list_countries():
            if not isinstance(row, dict):
                continue
            sc = str(row.get("shortcode") or "").strip().lower()
            slug = str(row.get("slug") or "").strip()
            if sc and slug:
                out[sc] = slug
    except Exception as exc:
        logger.warning("Failed loading country slugs from prodapi: %s", exc)
    return out


def _resolve_api_slug(token_or_shortcode: str, slug_map: dict[str, str]) -> str | None:
    key = (token_or_shortcode or "").strip().lower()
    if not key:
        return None
    sc = _TOKEN_TO_SHORTCODE.get(key, key)
    return slug_map.get(sc) or slug_map.get(key)


def is_country_compare_query(question: str) -> bool:
    return bool(_COMPARE_QUERY.search(question or ""))


def _slugs_from_question(
    question: str,
    *,
    client: ProdapiClient | None = None,
) -> list[str]:
    """Ordered unique country API slugs named in the question."""
    text = (question or "").lower()
    slug_map = _api_slug_by_shortcode(client)
    ordered: list[str] = []

    catalog = load_location_catalog()
    for name in sorted(catalog.keys(), key=len, reverse=True):
        if name.lower() not in text:
            continue
        info = catalog[name]
        sc = str(info.get("shortcode") or "").lower()
        slug = str(info.get("api_slug") or info.get("slug") or "").strip()
        if not slug:
            slug = _resolve_api_slug(sc, slug_map) or ""
        if slug and slug not in ordered:
            ordered.append(slug)

    if len(ordered) >= 2:
        return ordered

    tokens = mentioned_country_tokens(question)
    for tok in sorted(tokens, key=len, reverse=True):
        slug = _resolve_api_slug(tok, slug_map)
        if slug and slug not in ordered:
            ordered.append(slug)
    return ordered


def _norm_name(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        clean_text(value).lower().replace(" islands", " island"),
    )


def _peer_matches_slug(peer: dict[str, Any], slug: str) -> bool:
    peer_slug = str(peer.get("slug") or "").strip()
    if peer_slug.casefold() == (slug or "").casefold():
        return True
    sc = str(peer.get("shortcode") or "").strip().lower()
    slug_map = _api_slug_by_shortcode()
    if sc and slug_map.get(sc) and slug_map[sc].casefold() == (slug or "").casefold():
        return True
    name = _norm_name(str(peer.get("countryName") or ""))
    target = _norm_name(slug.replace("-", " "))
    if not name:
        return False
    if name == target or target in name or name in target:
        return True
    slug_l = (slug or "").casefold()
    if "cayman" in slug_l and "cayman" in name:
        return True
    if "virgin" in slug_l and ("virgin" in name or name == "bvi"):
        return True
    if slug_l in {"uae", "ae"} and name in {"uae", "united arab emirates"}:
        return True
    if "ireland" in slug_l and "ireland" in name:
        return True
    return False


def _comparison_map(peer: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in peer.get("comparison") or []:
        if not isinstance(row, dict):
            continue
        key = clean_text(str(row.get("key") or ""))
        val = clean_text(str(row.get("value") or ""))
        if key and val:
            out[key] = val
    return out


def _slug_already_in_matrix(slug: str, matrix: dict[str, dict[str, str]]) -> bool:
    for name in matrix:
        fake = {"countryName": name, "slug": "", "shortcode": ""}
        if _peer_matches_slug(fake, slug):
            return True
        # also try shortcode reverse via catalog
    return False


def build_country_compare_matrix(
    slugs: list[str],
    *,
    client: ProdapiClient | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, str], list[str]]:
    """Return matrix, source urls, and display-name order for requested slugs."""
    client = client or ProdapiClient()
    matrix: dict[str, dict[str, str]] = {}
    urls: dict[str, str] = {}
    order: list[str] = []
    all_peers: list[dict[str, Any]] = []

    for slug in slugs:
        try:
            overview = client.get_country_overview(slug)
        except Exception as exc:
            logger.warning("compare overview failed for %s: %s", slug, exc)
            continue
        peers = [p for p in (overview.get("compare") or []) if isinstance(p, dict)]
        all_peers.extend(peers)
        self_peer = next((p for p in peers if _peer_matches_slug(p, slug)), None)
        if self_peer is None:
            continue
        name = clean_text(str(self_peer.get("countryName") or slug))
        if name in matrix:
            continue
        matrix[name] = _comparison_map(self_peer)
        sc = str(self_peer.get("shortcode") or "").strip()
        urls[name] = country_page_url(sc or None, slug=slug)
        order.append(name)

    for slug in slugs:
        if _slug_already_in_matrix(slug, matrix):
            continue
        for peer in all_peers:
            if not _peer_matches_slug(peer, slug):
                continue
            name = clean_text(str(peer.get("countryName") or slug))
            if name in matrix:
                break
            matrix[name] = _comparison_map(peer)
            sc = str(peer.get("shortcode") or "").strip()
            urls[name] = country_page_url(sc or None, slug=slug)
            order.append(name)
            break

    return matrix, urls, order


def _ordered_keys(matrix: dict[str, dict[str, str]]) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for pref in _PREFERRED_KEYS:
        if any(pref in rows for rows in matrix.values()):
            keys.append(pref)
            seen.add(pref)
    for rows in matrix.values():
        for key in rows:
            if key not in seen:
                keys.append(key)
                seen.add(key)
    return keys


def build_country_compare_answer(
    question: str,
    *,
    client: ProdapiClient | None = None,
) -> tuple[str, list[RetrievedChunk]] | None:
    if not is_country_compare_query(question):
        return None
    slugs = _slugs_from_question(question, client=client)
    if len(slugs) < 2:
        return None

    matrix, urls, order = build_country_compare_matrix(slugs, client=client)
    if len(matrix) < 2:
        logger.warning(
            "Compare short-circuit: fewer than 2 countries resolved for %r slugs=%s",
            question[:80],
            slugs,
        )
        return None

    keys = _ordered_keys(matrix)
    names = [n for n in order if n in matrix] or list(matrix.keys())

    # HTML table for the portal (marked ΓåÆ answer-prose). Markdown pipes often
    # render as plain text without neat column alignment in the chat UI.
    head_cells = "".join(f"<th>{_html_escape(n)}</th>" for n in names)
    body_rows: list[str] = []
    for key in keys:
        cells = "".join(
            f"<td>{_html_escape(matrix.get(name, {}).get(key) or 'ΓÇö')}</td>"
            for name in names
        )
        body_rows.append(
            f"<tr><th scope=\"row\">{_html_escape(key)}</th>{cells}</tr>"
        )

    source_links = []
    for name in names:
        url = urls.get(name)
        if url:
            source_links.append(
                f'<li><a href="{_html_escape(url)}" target="_blank" '
                f'rel="noopener noreferrer">{_html_escape(name)}</a></li>'
            )

    answer = (
        f"<p>Comparison of <strong>{_html_escape(' vs '.join(names))}</strong> "
        "from Arnifi country overview tables:</p>\n"
        '<div class="compare-table-wrap">\n'
        '<table class="compare-table">\n'
        f"<thead><tr><th>Criteria</th>{head_cells}</tr></thead>\n"
        "<tbody>\n"
        + "\n".join(body_rows)
        + "\n</tbody>\n</table>\n</div>\n"
    )
    if source_links:
        answer += (
            "<p><strong>Source pages:</strong></p>\n"
            "<ul>\n" + "\n".join(source_links) + "\n</ul>"
        )
    chunks: list[RetrievedChunk] = []
    for i, name in enumerate(names, start=1):
        url = urls.get(name) or "https://arnifi.com/"
        body_lines = [f"Comparison peer: {name}"]
        for key in keys:
            val = matrix.get(name, {}).get(key)
            if val:
                body_lines.append(f"{key}: {val}")
        chunks.append(
            RetrievedChunk(
                chunk_id=f"compare-deterministic-{i}",
                source_url=url,
                doc_title=name,
                heading_path=f"compare > {name}",
                chunk_text="\n".join(body_lines),
                score=1.0 - (i * 0.01),
                metadata={
                    "source_type": "website",
                    "page_kind": "country_overview",
                    "catalog_section": "compare",
                    "deterministic_compare": True,
                },
            )
        )
    logger.info(
        "Deterministic compare answer for slugs=%s countries=%s metrics=%d",
        slugs,
        names,
        len(keys),
    )
    return answer, chunks
