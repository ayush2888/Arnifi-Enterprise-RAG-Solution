"""Tests for catalog section list retrieval (selling points / FAQ)."""

from __future__ import annotations

import unittest

from app.models.schemas import RetrievedChunk
from app.services.retrieval.catalog_focus import (
    apply_catalog_section_boost,
    catalog_list_limits,
    is_catalog_section_list_question,
    protected_chunk_ids_for_catalog_sections,
    target_catalog_sections,
)
from app.services.retrieval.diversify import diversify_chunks


def _chunk(
    *,
    chunk_id: str,
    score: float,
    heading: str,
    page_kind: str = "country_overview",
    source_type: str = "website",
    url: str = "https://arnifi.com/hk/",
    text: str = "body",
    doc_title: str | None = None,
) -> RetrievedChunk:
    title = doc_title
    if title is None:
        # Derive a simple title from the URL path so country filters are realistic.
        path = url.rstrip("/").rsplit("/", 1)[-1]
        title = path.upper() if len(path) <= 3 else path.replace("-", " ").title()
    return RetrievedChunk(
        chunk_id=chunk_id,
        score=score,
        source_url=url,
        doc_title=title,
        heading_path=heading,
        chunk_text=text,
        metadata={
            "page_kind": page_kind,
            "source_type": source_type,
            "heading_path": heading,
            "doc_title": title,
        },
    )


class TestCatalogFocus(unittest.TestCase):
    def test_detects_selling_points_and_faq_intent(self) -> None:
        self.assertTrue(
            is_catalog_section_list_question(
                "from the Hong Kong country page, list the 6 key selling points exactly"
            )
        )
        self.assertTrue(
            is_catalog_section_list_question(
                "Hong Kong FAQ from the location page: list all 5 questions"
            )
        )
        self.assertFalse(is_catalog_section_list_question("What is CEPA in Hong Kong?"))

    def test_target_sections(self) -> None:
        self.assertEqual(
            target_catalog_sections("Hong Kong key selling points"),
            frozenset({"selling_points"}),
        )
        self.assertEqual(
            target_catalog_sections("Hong Kong FAQ list"),
            frozenset({"faq", "faqs"}),
        )

    def test_protect_allows_all_selling_points_past_url_cap(self) -> None:
        q = "list the 6 key selling points for Hong Kong"
        selling = [
            _chunk(
                chunk_id=f"sp{i}",
                score=0.55 - i * 0.01,
                heading=f"selling_points > Point {i}",
                text=f"Selling point {i} " + ("x" * 40),
            )
            for i in range(6)
        ]
        blog = _chunk(
            chunk_id="blog",
            score=0.60,
            heading="Introduction",
            page_kind="",
            source_type="blog",
            url="https://arnifi.com/blog/hk-holding/",
            text="Holding company blog " + ("y" * 40),
        )
        matches = [blog, *selling]
        boosted = apply_catalog_section_boost(q, matches)
        protected = protected_chunk_ids_for_catalog_sections(q, boosted)
        self.assertEqual(len(protected), 6)

        max_returned, max_per_url, _ = catalog_list_limits(
            q,
            default_returned=5,
            default_per_url=2,
            list_returned=12,
            list_per_url=10,
            list_top_k=40,
            default_top_k=20,
        )
        out = diversify_chunks(
            boosted,
            max_chunks_returned=max_returned,
            max_chunks_per_source_url=max_per_url,
            protected_chunk_ids=protected,
        )
        ids = [c.chunk_id for c in out]
        for i in range(6):
            self.assertIn(f"sp{i}", ids)

    def test_packages_follow_up_targets_top_packages(self) -> None:
        from app.services.retrieval.catalog_focus import target_catalog_sections
        from app.services.retrieval.engine import (
            build_search_text,
            is_elliptical_follow_up,
        )

        self.assertTrue(is_elliptical_follow_up("top packages"))
        enriched = build_search_text(
            "top packages",
            [
                {"role": "user", "content": "Cyprus key selling points"},
                {"role": "assistant", "content": "ΓÇª"},
            ],
        )
        self.assertIn("Cyprus", enriched)
        self.assertEqual(
            target_catalog_sections(enriched),
            frozenset({"top_packages", "top_funds"}),
        )

    def test_country_filter_drops_other_locations(self) -> None:
        from app.services.retrieval.catalog_focus import (
            chunk_matches_country,
            filter_cross_country_catalog_noise,
            mentioned_country_tokens,
        )

        self.assertIn("hk", mentioned_country_tokens("Hong Kong key selling points"))
        q = "Hong Kong key selling points"
        hk = _chunk(
            chunk_id="hk1",
            score=0.5,
            heading="selling_points > CEPA",
            url="https://arnifi.com/hk/",
        )
        sg = _chunk(
            chunk_id="sg1",
            score=0.7,
            heading="selling_points > Trade Hub",
            url="https://arnifi.com/sg/",
            text="Singapore selling " + ("z" * 40),
        )
        out = filter_cross_country_catalog_noise(q, [sg, hk])
        ids = [c.chunk_id for c in out]
        self.assertIn("hk1", ids)
        self.assertNotIn("sg1", ids)

        # "cy" must not match Cayman /cym/
        cyprus_tokens = mentioned_country_tokens("Cyprus top packages")
        cym = _chunk(
            chunk_id="cym1",
            score=0.9,
            heading="top_packages > Cayman",
            url="https://arnifi.com/cym/",
            text="Cayman package " + ("z" * 40),
        )
        self.assertFalse(chunk_matches_country(cym, cyprus_tokens))
        cy = _chunk(
            chunk_id="cy1",
            score=0.5,
            heading="top_packages > Cyprus Co",
            url="https://arnifi.com/cy/",
            text="Cyprus package " + ("z" * 40),
        )
        filtered = filter_cross_country_catalog_noise(
            "Cyprus top packages", [cym, cy]
        )
        self.assertEqual([c.chunk_id for c in filtered], ["cy1"])


if __name__ == "__main__":
    unittest.main()
