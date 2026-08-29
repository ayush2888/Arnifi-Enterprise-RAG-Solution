"""Tests for website catalog extractors (no network)."""

from __future__ import annotations

from app.services.ingestion.pipeline import chunk_document
from app.services.ingestion.website import (
    extract_child_urls,
    extract_llms_document,
    extract_service_cards,
    extract_website_document,
)


CARD_HTML = """
<html><body>
<main>
<h1>Visa Services</h1>
<h3 class="t" aria-label="IFZA Employment Visa Inside Country">IFZA Employment Visa Inside...</h3>
<p>An IFZA in-country employment visa allows individuals already in the UAE.</p>
<p>Estimated time 15-20 days</p>
<p>starting from</p>
<p>AED 6,850</p>
<a href="/case-studies/can-logistics-companies-scale-operations-in-dmcc">DMCC story</a>
<a href="/cost-calculator">skip</a>
<a href="/blog/some-post">blog skip</a>
</main>
</body></html>
"""


def test_extract_service_cards_from_starting_from_block() -> None:
    html = """
    <div>
      <p>SHAMS - Standard Package - 1 Visa - 1 Year</p>
      <p>starting from</p>
      <p>AED 10,405</p>
    </div>
    """
    sections = extract_service_cards(html, "https://arnifi.com/pricing-master-list/")
    assert sections
    assert "10,405" in sections[0].text
    assert "SHAMS" in sections[0].heading_text
    sections = extract_service_cards(CARD_HTML, "https://arnifi.com/services/visa-service/")
    assert sections
    joined = " ".join(s.text for s in sections)
    assert "6,850" in joined
    assert "IFZA" in sections[0].heading_text


def test_extract_website_document_page_kind_and_chunks() -> None:
    doc = extract_website_document(
        CARD_HTML, "https://arnifi.com/services/visa-service/", "service"
    )
    chunks = chunk_document(doc, source_type="website", page_kind="service")
    assert chunks
    assert all(c.source_type == "website" for c in chunks)
    assert chunks[0].page_kind == "service"
    assert "PageKind: service" in chunks[0].embed_text
    assert "starting-from" in chunks[0].embed_text.lower() or "PriceDisclaimer" in chunks[0].embed_text


def test_extract_llms_document_splits_headings() -> None:
    text = "# Arnifi\n\nIntro line.\n\n## Core Services\n- Visa\n"
    doc = extract_llms_document(text, "https://arnifi.com/llms.txt")
    assert doc.title == "Arnifi"
    assert any(s.heading_text == "Core Services" for s in doc.sections)


def test_extract_child_urls_keeps_case_studies_not_tools() -> None:
    urls = extract_child_urls(
        CARD_HTML,
        "https://arnifi.com/services/visa-service/",
        allowed_domains=["arnifi.com"],
    )
    assert any("case-studies" in u for u in urls)
    assert not any("cost-calculator" in u for u in urls)
    assert not any("/blog/" in u for u in urls)
