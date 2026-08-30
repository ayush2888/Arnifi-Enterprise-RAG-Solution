"""Tests for prodapi micro-service / setup product ΓåÆ Document mapping (no network)."""

from __future__ import annotations

import unittest

from app.services.ingestion.website_sections import (
    extract_micro_service_from_api,
    extract_setup_product_from_api,
)
from app.services.prodapi.client import micro_service_page_url, setup_product_page_url


MICRO_SERVICE_PAYLOAD = {
    "id": 42,
    "title": "Guernsey Authorised Closed-ended Collective Investment Schemes",
    "slug": "guernsey-authorised-closed-ended-collective-investment-schemes",
    "type": "Funds",
    "description": "Authorised closed-ended schemes for professional investors.",
    "shortDescription": "Closed-ended fund setup in Guernsey.",
    "highlightsAndBenifits": [
        {
            "heading": "Regulatory credibility",
            "description": "GFSC oversight with clear investor protections.",
        }
    ],
    "processFlow": [
        {
            "heading": "Submit application",
            "description": "File scheme particulars with the authority.",
        },
        {
            "heading": "Receive approval",
            "description": "Authority reviews and issues authorisation.",
        },
    ],
    "faqs": [
        {
            "question": "Who can invest?",
            "answer": "Typically professional and institutional investors.",
        }
    ],
    "termsAndConditions": [
        {"termAndCondition": "Starting-from fees exclude government charges."}
    ],
    "documentsFromAuthority": [
        {"heading": "Scheme particulars", "description": "Draft offering document."}
    ],
    "documentsRequired": [
        {"name": "Certificate of Incorporation", "description": None},
        {"name": "Business Profile (latest)", "description": None},
        {"name": "Constitution", "description": None},
    ],
    "price": 12000,
    "currency": "USD",
    "estimatedDeliveryTime": "4-6 weeks",
}


SETUP_PRODUCT_PAYLOAD = {
    "id": 99,
    "attributes": {
        "productName": "IFZA Standard Package - 1 Visa",
        "slug": "ifza-standard-package-1-visa",
        "productType": "licence",
        "description": "IFZA mainland-style free zone package with one visa.",
        "faqs": [],
        "startingArnifiPrice": 10405,
        "currency": "AED",
        "pricingPlan": {"pricing": [{"name": "Year 1", "amount": 10405}]},
    },
}


class TestProdapiServices(unittest.TestCase):
    def test_micro_service_page_url(self) -> None:
        url = micro_service_page_url("Visa Services", "ifza-employment-visa", service_id=7)
        self.assertEqual(
            url,
            "https://arnifi.com/product-details/services/visa-services/ifza-employment-visa/7",
        )

    def test_setup_product_page_url(self) -> None:
        url = setup_product_page_url(
            "ifza-standard",
            product_id=99,
            country_slug="uae",
        )
        self.assertEqual(
            url,
            "https://arnifi.com/product-details/business-setup/uae/ifza-standard/99",
        )

    def test_extract_micro_service_sections(self) -> None:
        source_url = micro_service_page_url(
            "Funds",
            MICRO_SERVICE_PAYLOAD["slug"],
            service_id=MICRO_SERVICE_PAYLOAD["id"],
        )
        doc = extract_micro_service_from_api(MICRO_SERVICE_PAYLOAD, source_url)

        self.assertEqual(doc.category_name, "Funds")
        labels = {s.heading_path[0] for s in doc.sections}
        self.assertEqual(
            labels,
            {
                "introduction",
                "documents_required",
                "highlights_and_benefits",
                "process_flow",
                "faqs",
                "terms_and_conditions",
                "documents_from_authority",
                "pricing",
            },
        )
        joined = "\n".join(s.text for s in doc.sections)
        self.assertIn("Regulatory credibility", joined)
        self.assertIn("Who can invest?", joined)
        self.assertIn("Certificate of Incorporation", joined)
        self.assertIn("1. Certificate of Incorporation", joined)
        self.assertIn("12000", joined.replace(",", ""))
        self.assertIn("USD", joined)
        self.assertIn("4-6 weeks", joined)

    def test_extract_setup_product_thin_payload(self) -> None:
        source_url = setup_product_page_url(
            "ifza-standard-package-1-visa",
            product_id=99,
            country_slug="uae",
        )
        doc = extract_setup_product_from_api(SETUP_PRODUCT_PAYLOAD, source_url)
        labels = {s.heading_path[0] for s in doc.sections}
        self.assertIn("introduction", labels)
        self.assertIn("pricing", labels)
        joined = "\n".join(s.text for s in doc.sections)
        self.assertIn("10405", joined.replace(",", ""))
        self.assertIn("IFZA", joined)


if __name__ == "__main__":
    unittest.main()
