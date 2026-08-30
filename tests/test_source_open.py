"""Tests for source URL sanitization / openability."""

from __future__ import annotations

import unittest

from app.utils.source_open import enrich_openable_sources, sanitize_source_url


class TestSourceOpen(unittest.TestCase):
    def test_rejects_relative_product_path(self) -> None:
        self.assertIsNone(
            sanitize_source_url(
                "/product-details/services/accounting-bookkeeping/singapore-statutory-audit/404"
            )
        )

    def test_keeps_arnifi_absolute(self) -> None:
        url = "https://arnifi.com/product-details/services/accounting-bookkeeping/singapore-statutory-audit/404/"
        clean = sanitize_source_url(url)
        self.assertEqual(
            clean,
            "https://arnifi.com/product-details/services/accounting-bookkeeping/singapore-statutory-audit/404",
        )

    def test_normalizes_drive_viewer(self) -> None:
        url = "https://drive.google.com/file/d/1jbzxyrWFYhUNPZlR2OkGX7k5fDofRCCg/view"
        clean = sanitize_source_url(url)
        self.assertIsNotNone(clean)
        assert clean is not None
        self.assertIn("/file/d/1jbzxyrWFYhUNPZlR2OkGX7k5fDofRCCg/view", clean)
        self.assertIn("usp=sharing", clean)

    def test_enrich_flags(self) -> None:
        rows = enrich_openable_sources(
            [
                {
                    "source_index": 1,
                    "source_url": "https://arnifi.com/product-details/services/x/y/1/",
                    "source_type": "website",
                },
                {
                    "source_index": 2,
                    "source_url": "/relative/bad",
                    "source_type": "website",
                },
                {
                    "source_index": 3,
                    "source_url": None,
                    "source_type": "website",
                    "snippet": "only snippet",
                },
            ]
        )
        self.assertTrue(rows[0]["can_open"])
        self.assertTrue(rows[0].get("use_portal_preview"))
        self.assertIn("2529575321", rows[0]["open_note"])
        self.assertFalse(rows[1]["can_open"])
        self.assertIsNone(rows[1].get("open_url"))
        self.assertFalse(rows[2]["can_open"])
        self.assertIn("no public web link", rows[2]["open_note"].lower())

    def test_blog_normalized_to_website_with_preview(self) -> None:
        rows = enrich_openable_sources(
            [
                {
                    "source_url": "https://arnifi.com/blog/some-post",
                    "source_type": "blog",
                }
            ]
        )
        self.assertEqual(rows[0]["source_type"], "website")
        self.assertTrue(rows[0].get("use_portal_preview"))


if __name__ == "__main__":
    unittest.main()
