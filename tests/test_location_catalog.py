"""Tests for location fund/package catalog counts."""

from __future__ import annotations

import unittest

from app.services.retrieval.location_catalog import (
    DEFAULT_INDEX_PATH,
    build_location_catalog_count_answer,
    export_location_catalog_index,
    is_location_catalog_count_query,
    load_location_catalog,
)


class TestLocationCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DEFAULT_INDEX_PATH.exists():
            export_location_catalog_index()
        load_location_catalog.cache_clear()

    def test_detects_fund_count_query(self) -> None:
        self.assertTrue(
            is_location_catalog_count_query("Luxembourg how many funds are there")
        )
        self.assertTrue(
            is_location_catalog_count_query("how many packages in the UAE?")
        )

    def test_luxembourg_has_four_funds(self) -> None:
        catalog = load_location_catalog()
        self.assertIn("Luxembourg", catalog)
        self.assertEqual(catalog["Luxembourg"]["fund_count"], 4)
        hit = build_location_catalog_count_answer(
            "Luxembourg how many funds are there"
        )
        self.assertIsNotNone(hit)
        answer, _chunks = hit  # type: ignore[misc]
        self.assertIn("**4**", answer)
        self.assertIn("Luxembourg RAIF", answer)
        self.assertIn("SOPARFI", answer)

    def test_uae_and_saudi_full_package_counts(self) -> None:
        catalog = load_location_catalog()
        uae = catalog.get("UAE", {}).get("package_count", 0)
        if uae < 70:
            self.skipTest("Live full licence index not built yet")
        self.assertEqual(catalog["UAE"]["package_count"], 78)
        self.assertEqual(catalog["Saudi Arabia"]["package_count"], 6)
        hit = build_location_catalog_count_answer("how many packages in the UAE")
        self.assertIsNotNone(hit)
        self.assertIn("**78**", hit[0])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
