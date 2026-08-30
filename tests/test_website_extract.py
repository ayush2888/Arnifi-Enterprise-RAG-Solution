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


COUNTRY_HTML = """
<html><body><main>
<h1>Guernsey</h1>
<script>
self.__next_f.push([1,"{\\"funds\\":[{\\"id\\":451,\\"title\\":\\"Guernsey Authorised Closed-ended Collective Investment Schemes\\",\\"slug\\":\\"guernsey-authorised-closed-ended-collective-investment-schemes\\",\\"startingPrice\\":12000,\\"currency\\":\\"USD\\",\\"banner\\":{\\"url\\":\\"x\\"},\\"authorityId\\":46,\\"authorityName\\":\\"Guernsey\\"},{\\"id\\":448,\\"title\\":\\"Guernsey Open-ended Collective Investment Schemes\\",\\"slug\\":\\"guernsey-open-ended-collective-investment-schemes\\",\\"startingPrice\\":13750,\\"currency\\":\\"USD\\",\\"banner\\":{\\"url\\":\\"x\\"},\\"authorityId\\":46,\\"authorityName\\":\\"Guernsey\\"}],\\"productPages\\":[],\\"processStep\\":[{\\"id\\":1,\\"question\\":\\"Select Your Business Structure\\",\\"answer\\":\\"Choose the appropriate entity based on your investment objectives\\"},{\\"id\\":2,\\"question\\":\\"Complete Company Registration\\",\\"answer\\":\\"Submit incorporation documents and satisfy regulatory requirements\\"}],\\"FAQs\\":[{\\"id\\":10,\\"question\\":\\"Why is Guernsey popular for international business?\\",\\"answer\\":\\"Guernsey offers tax efficiency and regulatory credibility.\\"}],\\"marketInsights\\":[{\\"id\\":29,\\"title\\":\\"Fund Regulation Updates\\",\\"text\\":\\"Guernsey introduced updated fund regulatory developments.\\",\\"tag\\":\\"Compliance\\",\\"date\\":\\"2026-06-01\\"}],\\"keySellingPoints\\":[{\\"id\\":89,\\"value\\":\\"Zero Direct Taxation No capital gains tax for most businesses\\"}],\\"prosCons\\":[]}"]);
</script>
</main></body></html>
"""

PACKAGE_HTML = """
<html><body><main>
<h1>IFZA Employment Visa</h1>
<h2>Introduction</h2>
<p>An IFZA in-country employment visa allows individuals already in the UAE to convert status.</p>
<h2>Documents required</h2>
<p>Passport copy</p>
<p>Photo with white background</p>
<h2>Highlights and benefits</h2>
<p>Fast processing and dedicated support for in-country applicants.</p>
<h2>Process flow</h2>
<p>Submit documents, receive approval, complete biometrics.</p>
<h2>Frequently asked questions</h2>
<p>How long does it take? Typically 15-20 working days depending on authority queues.</p>
<h2>Terms and conditions</h2>
<p>Fees are starting-from estimates and exclude government surcharges.</p>
<h2>Pricing details</h2>
<p>starting from AED 2,254.36</p>
</main></body></html>
"""


def test_country_overview_sections_and_faq_pairs() -> None:
    from app.services.ingestion.website_sections import extract_country_overview_document

    doc = extract_country_overview_document(COUNTRY_HTML, "https://arnifi.com/gg/")
    labels = [s.heading_path[0] for s in doc.sections]
    assert "market_insights" in labels
    assert "selling_points" in labels
    assert "process_flow" in labels
    assert "top_funds" in labels
    faqs = [s for s in doc.sections if s.heading_path and s.heading_path[0] == "faq"]
    assert len(faqs) >= 1
    joined = " ".join(s.text for s in doc.sections)
    assert "12000" in joined.replace(",", "") or "12,000" in joined
    assert "closed-ended" in joined.lower()
    chunks = chunk_document(doc, source_type="website", page_kind="country_overview")
    assert chunks
    assert all(c.page_kind == "country_overview" for c in chunks)


def test_country_catalog_discovers_fund_detail_urls() -> None:
    from app.services.ingestion.website_sections import country_catalog_detail_urls

    urls = country_catalog_detail_urls(COUNTRY_HTML, "https://arnifi.com/gg/")
    assert any("guernsey-authorised-closed-ended" in u for u in urls)
    assert any("/product-details/funds/guernsey/" in u for u in urls)


def test_package_detail_documents_required() -> None:
    from app.services.ingestion.website_sections import (
        extract_package_detail_document,
        missing_package_sections,
    )

    doc, missing = extract_package_detail_document(
        PACKAGE_HTML,
        "https://arnifi.com/services/visa-service/ifza-employment-visa/",
        "service_package",
    )
    labels = {s.heading_path[0] for s in doc.sections}
    assert "introduction" in labels
    assert "documents_required" in labels
    assert "Passport" in " ".join(s.text for s in doc.sections)
    leftover = missing_package_sections(doc)
    assert "introduction" not in leftover
    assert missing is not None


def test_homepage_collects_location_and_service_hubs() -> None:
    html = """
    <html><body>
    <a href="/ae">UAE</a>
    <a href="/services/accounting">Accounting</a>
    <a href="/cost-calculator">calc</a>
    </body></html>
    """
    urls = extract_child_urls(
        html,
        "https://arnifi.com/",
        allowed_domains=["arnifi.com"],
        collect_hubs=True,
    )
    joined = " ".join(urls)
    assert "/ae" in joined
    assert "accounting" in joined
    assert "cost-calculator" not in joined
