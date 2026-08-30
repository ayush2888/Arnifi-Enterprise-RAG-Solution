"""Tests for Company/Fund catalog discovery + lineage metadata."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.models.schemas import Document, Section
from app.services.ingestion.company_fund_catalog import (
    map_fund_listing_row,
    map_licence_listing_row,
)
from app.services.ingestion.pipeline import chunk_document
from app.services.prodapi.client import ProdapiClient


class TestCompanyFundListingMaps(unittest.TestCase):
    def test_map_licence_row(self) -> None:
        row = {
            "id": 4,
            "attributes": {
                "productName": "SRTIP Innovators Package",
                "productType": "licence",
                "slug": "srtip-innovators-package",
                "startingArnifiPrice": 6510,
                "currency": "AED",
                "bestSellerRank": 2,
                "country": {
                    "data": {
                        "id": 1,
                        "attributes": {"name": "UAE", "slug": "uae"},
                    }
                },
            },
        }
        card = map_licence_listing_row(row)
        self.assertEqual(card["product_type"], "licence")
        self.assertEqual(card["package_name"], "SRTIP Innovators Package")
        self.assertEqual(card["country"], "UAE")
        self.assertEqual(card["starting_price"], "AED 6510")
        self.assertIn("product-details", card["detail_url"])
        self.assertEqual(card["tags"], ["Best Seller"])
        self.assertEqual(card["discovered_via"], "product-listing")

    def test_map_fund_row_badge_is_jurisdiction_not_tag(self) -> None:
        row = {
            "id": 394,
            "slug": "adgm-multi-family-office-setup",
            "badgeLabel": "UAE",
            "startingPrice": 32850,
            "currency": "USD",
            "estimatedDeliveryTime": "5 - 6 Months",
            "bottomTitle": "ADGM Multi Family Office Setup",
            "bestSellerRank": None,
        }
        card = map_fund_listing_row(row)
        self.assertEqual(card["product_type"], "fund")
        self.assertEqual(card["jurisdiction"], "UAE")
        self.assertEqual(card["tags"], [])
        self.assertEqual(card["discovered_via"], "funds-catalog")
        self.assertIn("/funds/", card["detail_url"])


class TestProductTypeMetadata(unittest.TestCase):
    def test_chunk_document_carries_lineage(self) -> None:
        doc = Document(
            title="Pkg",
            source_url="https://arnifi.com/product-details/services/funds/x/1",
            category_name="Funds",
            sections=[
                Section(
                    section_id="introduction",
                    heading_path=["Introduction"],
                    heading_text="Introduction",
                    text="Hello fund package body text for chunking.",
                )
            ],
        )
        chunks = chunk_document(
            doc,
            page_kind="product_detail",
            source_type="website",
            product_type="fund",
            discovered_via="funds-catalog",
        )
        self.assertTrue(chunks)
        self.assertEqual(chunks[0].product_type, "fund")
        self.assertEqual(chunks[0].discovered_via, "funds-catalog")
        meta = chunks[0].metadata()
        self.assertEqual(meta["product_type"], "fund")
        self.assertEqual(meta["discovered_via"], "funds-catalog")


class TestListProductPagesPagination(unittest.TestCase):
    def test_list_product_pages_paginates(self) -> None:
        client = ProdapiClient()
        calls: list[dict] = []

        def fake_get(path: str, *, params=None):
            calls.append(params or {})
            page = int((params or {}).get("pagination[page]") or 1)
            if page == 1:
                return {
                    "data": [{"id": 1, "attributes": {"slug": "a", "productType": "licence"}}],
                    "meta": {"pagination": {"page": 1, "pageCount": 2, "total": 2}},
                }
            return {
                "data": [{"id": 2, "attributes": {"slug": "b", "productType": "licence"}}],
                "meta": {"pagination": {"page": 2, "pageCount": 2, "total": 2}},
            }

        with patch.object(client, "_get", side_effect=fake_get):
            rows = client.list_product_pages(product_type="licence")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["filters[productType][$eq]"], "licence")
        self.assertEqual(calls[0]["populate[country]"], "*")


class TestCompletenessGate(unittest.TestCase):
    def test_ingest_aborts_when_live_total_mismatches(self) -> None:
        from app.services.ingestion.prodapi_services import ProdapiServicesIndexer

        settings = MagicMock()
        indexer = ProdapiServicesIndexer(settings, client=MagicMock())
        fake_cat = {
            "product_type": "fund",
            "listing_url": "https://arnifi.com/services/funds",
            "discovery": "prodapi",
            "live_stated_total": 29,
            "discovered": 41,
            "complete": False,
            "items": [],
            "detail_urls": [],
        }
        with patch(
            "app.services.ingestion.company_fund_catalog.discover_funds_catalog",
            return_value=fake_cat,
        ), patch(
            "app.services.ingestion.company_fund_catalog.discover_licence_catalog",
            return_value={
                "product_type": "licence",
                "listing_url": "https://arnifi.com/product-listing?productType=licence",
                "discovery": "prodapi",
                "live_stated_total": 117,
                "discovered": 117,
                "complete": True,
                "items": [],
                "detail_urls": [],
            },
        ):
            result = indexer.ingest_company_fund_catalogs(funds_only=True)
        self.assertTrue(result.get("aborted"))
        self.assertEqual(result.get("reason"), "completeness_gate")


if __name__ == "__main__":
    unittest.main()
