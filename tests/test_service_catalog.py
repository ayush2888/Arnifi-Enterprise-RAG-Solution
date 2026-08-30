"""Tests for service catalog counts and elliptical follow-ups."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.retrieval.engine import build_search_text, is_elliptical_follow_up
from app.services.retrieval.service_catalog import (
    build_catalog_count_answer,
    export_service_catalog_index,
    is_service_catalog_count_query,
    load_service_catalog,
    match_service_name,
)


class TestServiceCatalogCount(unittest.TestCase):
    def test_detects_count_queries(self) -> None:
        self.assertTrue(is_service_catalog_count_query("how many packages in attestation"))
        self.assertTrue(is_service_catalog_count_query("How many packages in Legal Services?"))
        self.assertFalse(is_service_catalog_count_query("What is the process flow for ADGM?"))

    def test_match_service_and_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gt = Path(tmp) / "gt.json"
            idx = Path(tmp) / "catalog.json"
            gt.write_text(
                json.dumps(
                    {
                        "packages": [
                            {
                                "service": "Attestation",
                                "service_url": "https://arnifi.com/services/attestations",
                                "package_name": f"Pkg {i}",
                                "package_slug": f"pkg-{i}",
                                "package_id": i,
                            }
                            for i in range(1, 38)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            export_service_catalog_index(gt, idx)
            load_service_catalog.cache_clear()
            catalog = load_service_catalog(str(idx), str(gt))
            self.assertEqual(catalog["Attestation"]["count"], 37)
            self.assertEqual(
                match_service_name("how many packages in attestation", list(catalog)),
                "Attestation",
            )
            # Point default paths via explicit load already tested; answer uses cached path
            # so temporarily monkey by writing to DEFAULT via export then clear.
            from app.services.retrieval import service_catalog as sc

            sc.DEFAULT_INDEX_PATH = idx
            sc.DEFAULT_GT_PATH = gt
            load_service_catalog.cache_clear()
            hit = build_catalog_count_answer("how many packages in attestation?")
            self.assertIsNotNone(hit)
            answer, chunks = hit  # type: ignore[misc]
            self.assertIn("37", answer)
            self.assertIn("Attestation", answer)
            self.assertTrue(chunks)


class TestEllipticalDocumentsFollowUp(unittest.TestCase):
    def test_documents_required_is_elliptical(self) -> None:
        self.assertTrue(is_elliptical_follow_up("what documents are required"))
        self.assertTrue(is_elliptical_follow_up("What documents are required?"))
        self.assertTrue(is_elliptical_follow_up("documents needed"))
        self.assertFalse(
            is_elliptical_follow_up("what documents are required for BVI company formation")
        )

    def test_search_text_inherits_prior_package(self) -> None:
        history = [
            {"role": "user", "content": "ADGM Company Liquidation price"},
            {"role": "assistant", "content": "Starting from AED ΓÇª"},
        ]
        text = build_search_text("what documents are required", history)
        self.assertIn("ADGM Company Liquidation", text)
        self.assertIn("what documents are required", text)


if __name__ == "__main__":
    unittest.main()
