"""Tests for prodapi country-overview ΓåÆ Document mapping (no network)."""

from __future__ import annotations

import unittest

from app.services.ingestion.website_sections import extract_country_overview_from_api
from app.services.prodapi.client import country_page_url


GUERNSEY_API_PAYLOAD = {
    "id": 15,
    "title": "Guernsey",
    "subHeader": "Build your Guernsey company with Arnifi.",
    "overviewDetail": (
        "Guernsey is a leading international finance centre known for its "
        "political stability and expertise in investment funds."
    ),
    "marketInsights": [
        {
            "id": 29,
            "title": "Fund Regulation Updates",
            "text": (
                "Guernsey introduced updated fund regulatory developments, "
                "reinforcing governance and investor protection."
            ),
            "tag": "Compliance",
            "date": "2026-06-01",
        }
    ],
    "keySellingPoints": [
        {
            "id": 89,
            "value": (
                "Zero Direct Taxation\nNo capital gains tax, inheritance tax, "
                "or VAT for most businesses."
            ),
        }
    ],
    "processStep": [
        {
            "id": 1,
            "question": "Select Your Business Structure",
            "answer": "Choose the appropriate entity based on your objectives.",
        },
        {
            "id": 2,
            "question": "Complete Company Registration",
            "answer": "Submit incorporation documents and satisfy requirements.",
        },
    ],
    "FAQs": [
        {
            "id": 132,
            "question": "Why is Guernsey popular for international business?",
            "answer": (
                "Guernsey offers tax efficiency, regulatory credibility, "
                "and a developed financial services ecosystem."
            ),
        }
    ],
    "funds": [
        {
            "id": 451,
            "title": "Guernsey Authorised Closed-ended Collective Investment Schemes",
            "slug": "guernsey-authorised-closed-ended-collective-investment-schemes",
            "startingPrice": 12000,
            "currency": "USD",
            "authorityName": "Guernsey",
        }
    ],
    "productPages": [],
    "compare": [
        {
            "countryName": "Guernsey",
            "slug": "guernsey",
            "comparison": [
                {"key": "Setup Timeline", "value": "1ΓÇô5 days"},
                {"key": "Capital Requirement", "value": "Low / no minimum"},
                {"key": "Corporate Tax Rate", "value": "0% for most companies"},
            ],
        },
        {
            "countryName": "Cayman Island",
            "slug": "cayman-island",
            "comparison": [
                {"key": "Setup Timeline", "value": "2ΓÇô5 days"},
                {"key": "Corporate Tax Rate", "value": "0%"},
            ],
        },
    ],
}


class TestProdapiLocations(unittest.TestCase):
    def test_country_page_url_from_shortcode(self) -> None:
        self.assertEqual(country_page_url("gg"), "https://arnifi.com/gg")
        self.assertEqual(country_page_url("MU"), "https://arnifi.com/mu")

    def test_extract_country_overview_from_api_section_labels(self) -> None:
        source_url = "https://arnifi.com/gg"
        doc = extract_country_overview_from_api(GUERNSEY_API_PAYLOAD, source_url)

        self.assertEqual(doc.title, "Guernsey")
        self.assertEqual(doc.category_name, "country_overview")
        self.assertEqual(doc.source_url.rstrip("/"), source_url)

        labels = {sec.heading_path[0] for sec in doc.sections}
        self.assertIn("overview", labels)
        self.assertIn("faq", labels)
        self.assertIn("top_funds", labels)
        self.assertIn("process_flow", labels)
        self.assertIn("market_insights", labels)
        self.assertIn("selling_points", labels)
        self.assertIn("compare", labels)

        faqs = [s for s in doc.sections if s.heading_path[0] == "faq"]
        self.assertTrue(faqs)
        self.assertTrue(faqs[0].text.startswith("Q:"))
        self.assertIn("\nA:", faqs[0].text)

        funds = [s for s in doc.sections if s.heading_path[0] == "top_funds"]
        self.assertTrue(funds)
        self.assertIn("12,000", funds[0].text)
        self.assertIn("guernsey-authorised-closed-ended", funds[0].text)

        compare = [s for s in doc.sections if s.heading_path[0] == "compare"]
        self.assertTrue(compare)
        joined = "\n".join(s.text for s in compare)
        self.assertIn("Setup Timeline", joined)
        self.assertIn("1ΓÇô5 days", joined)
        self.assertIn("Cayman Island", joined)
        # Full table + per-peer + per-metric chunks
        self.assertGreaterEqual(len(compare), 3)


if __name__ == "__main__":
    unittest.main()
