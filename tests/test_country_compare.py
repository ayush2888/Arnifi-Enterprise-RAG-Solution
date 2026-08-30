"""Tests for deterministic country compare answers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.retrieval.country_compare import (
    build_country_compare_answer,
    build_country_compare_matrix,
    is_country_compare_query,
    _slugs_from_question,
)


def _peer(name: str, slug: str, shortcode: str, rows: list[tuple[str, str]]) -> dict:
    return {
        "countryName": name,
        "slug": slug,
        "shortcode": shortcode,
        "comparison": [{"key": k, "value": v} for k, v in rows],
    }


CAYMAN_OVERVIEW = {
    "compare": [
        _peer(
            "Cayman Island",
            "cayman-islands",
            "cym",
            [
                ("Setup Timeline", "2ΓÇô5 days"),
                ("Visa Application", "Not applicable"),
                ("Tax Incentives", "Tax-neutral regime"),
                ("Banking", "Moderate to difficult"),
            ],
        ),
        _peer(
            "British Virgin Islands",
            "british-virgin-islands",
            "vg",
            [
                ("Setup Timeline", "1ΓÇô3 days"),
                ("Visa Application", "Not applicable"),
                ("Tax Incentives", "Tax-neutral regime"),
                ("Banking", "Moderate to difficult"),
            ],
        ),
        _peer(
            "UAE",
            "uae",
            "ae",
            [
                ("Setup Timeline", "3ΓÇô10 days"),
                ("Visa Application", "Linked to the company"),
                ("Tax Incentives", "Free zone benefits"),
                ("Banking", "Moderate difficulty"),
            ],
        ),
    ]
}


class TestCountryCompare(unittest.TestCase):
    def test_detect_compare_query(self) -> None:
        self.assertTrue(
            is_country_compare_query(
                "Compare Cayman Island with British Virgin Islands & UAE"
            )
        )
        self.assertFalse(is_country_compare_query("how many packages in UAE"))

    def test_slugs_from_question(self) -> None:
        slugs = _slugs_from_question(
            "Compare Cayman Island with British Virgin Islands & UAE"
        )
        # Exact API slugs (case-sensitive)
        self.assertEqual(
            set(s.casefold() for s in slugs),
            {"cayman-islands", "british-virgin-islands", "uae"},
        )

    def test_ireland_slug_is_title_case(self) -> None:
        slugs = _slugs_from_question(
            "Compare Ireland with British Virgin Islands & Cayman Island"
        )
        self.assertTrue(any(s == "Ireland" or s.casefold() == "ireland" for s in slugs))
        # Must not use lowercase-only ireland if API requires Ireland
        self.assertIn("Ireland", slugs)

    def test_matrix_has_all_three(self) -> None:
        client = MagicMock()
        client.get_country_overview.side_effect = lambda slug: {
            "cayman-islands": CAYMAN_OVERVIEW,
            "british-virgin-islands": {"compare": CAYMAN_OVERVIEW["compare"]},
            "uae": {"compare": [CAYMAN_OVERVIEW["compare"][2]]},
        }[slug]
        matrix, _urls, order = build_country_compare_matrix(
            ["cayman-islands", "british-virgin-islands", "uae"],
            client=client,
        )
        self.assertEqual(len(matrix), 3)
        self.assertIn("Cayman Island", matrix)
        self.assertEqual(matrix["Cayman Island"]["Setup Timeline"], "2ΓÇô5 days")
        self.assertEqual(matrix["UAE"]["Tax Incentives"], "Free zone benefits")

    def test_answer_includes_cayman_fields(self) -> None:
        client = MagicMock()
        client.get_country_overview.side_effect = lambda slug: {
            "cayman-islands": CAYMAN_OVERVIEW,
            "british-virgin-islands": {"compare": CAYMAN_OVERVIEW["compare"]},
            "uae": {"compare": [CAYMAN_OVERVIEW["compare"][2]]},
        }[slug]
        hit = build_country_compare_answer(
            "Compare Cayman Island with British Virgin Islands & UAE",
            client=client,
        )
        self.assertIsNotNone(hit)
        answer, chunks = hit
        self.assertIn("2ΓÇô5 days", answer)
        self.assertIn("Free zone benefits", answer)
        self.assertIn("Cayman Island", answer)
        self.assertIn("<table", answer)
        self.assertIn("compare-table", answer)
        self.assertNotIn("Not provided", answer)
        self.assertGreaterEqual(len(chunks), 3)


if __name__ == "__main__":
    unittest.main()
