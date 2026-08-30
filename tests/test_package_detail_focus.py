"""Tests for package-detail retrieval focus (demote Pricing Master)."""

from __future__ import annotations

import unittest

from app.models.schemas import RetrievedChunk
from app.services.retrieval.fee_focus import apply_fee_focus, is_fee_or_payer_query
from app.services.retrieval.package_detail_focus import (
    apply_package_detail_focus,
    diversify_package_detail_sections,
    is_package_detail_query,
)


def _chunk(
    *,
    chunk_id: str,
    score: float,
    title: str,
    text: str,
    url: str,
    page_kind: str | None = None,
    heading: str = "introduction",
) -> RetrievedChunk:
    meta: dict = {"heading_path": heading}
    if page_kind:
        meta["page_kind"] = page_kind
        meta["source_type"] = "website"
        meta["catalog_section"] = heading.split(">")[0].strip()
        meta["catalog_package"] = title
    return RetrievedChunk(
        chunk_id=chunk_id,
        score=score,
        source_url=url,
        doc_title=title,
        heading_path=heading,
        chunk_text=text,
        metadata=meta,
    )


class TestPackageDetailFocus(unittest.TestCase):
    def test_detects_package_detail_query(self) -> None:
        q = (
            "From Arnifi's package detail page for 'Singapore Basic Incorporation', "
            "summarize the introduction, highlights/benefits, process flow, FAQs, "
            "and terms if available. Include pricing if shown."
        )
        self.assertTrue(is_package_detail_query(q))
        self.assertTrue(is_fee_or_payer_query(q))  # "pricing" still matches fee regex
        self.assertFalse(is_package_detail_query("What is the IQAMA Medical Authority fee?"))

    def test_drops_pricing_master_and_keeps_product_detail(self) -> None:
        q = (
            "From Arnifi's package detail page for 'Singapore Basic Incorporation', "
            "summarize the introduction, highlights/benefits, process flow, FAQs, "
            "and terms if available. Include pricing if shown."
        )
        pricing = _chunk(
            chunk_id="price",
            score=0.95,
            title="Pricing Master Sheet - Company Setup (1).csv",
            text="Title: Singapore Basic Incorporation\nAuthority fees: 100",
            url="https://drive.google.com/file/d/abc/view",
            heading="Knowledge Base/Pricing Master Sheet",
        )
        product = _chunk(
            chunk_id="prod",
            score=0.70,
            title="Singapore Basic Incorporation",
            text="Singapore stands out as one of the world's most attractive destinations.",
            url="https://arnifi.com/product-details/business-setup/business-setup/singapore-basic-incorporation/99/",
            page_kind="product_detail",
            heading="introduction",
        )
        # Fee focus alone would prefer Pricing Master
        fee_ordered = apply_fee_focus(q, [pricing, product])
        self.assertEqual(fee_ordered[0].chunk_id, "price")

        focused = apply_package_detail_focus(q, [pricing, product])
        self.assertEqual(focused[0].chunk_id, "prod")
        self.assertTrue(all(c.chunk_id != "price" for c in focused))

    def test_section_diversify_prefers_one_per_section(self) -> None:
        q = (
            "From Arnifi's package detail page for 'IFZA License', "
            "summarize the introduction, highlights/benefits, process flow, FAQs."
        )
        url = "https://arnifi.com/product-details/business-setup/x/ifza/5/"
        chunks = [
            _chunk(
                chunk_id=f"faq{i}",
                score=0.9 - i * 0.01,
                title="IFZA License",
                text=f"Q: faq {i}\nA: answer",
                url=url,
                page_kind="product_detail",
                heading="faqs",
            )
            for i in range(4)
        ] + [
            _chunk(
                chunk_id="intro",
                score=0.6,
                title="IFZA License",
                text="Introduction body",
                url=url,
                page_kind="product_detail",
                heading="introduction",
            )
        ]
        ordered = diversify_package_detail_sections(q, chunks, max_chunks=4)
        ids = [c.chunk_id for c in ordered]
        self.assertIn("faq0", ids)
        self.assertIn("intro", ids)
        self.assertEqual(len([i for i in ids if i.startswith("faq")]), 1)


if __name__ == "__main__":
    unittest.main()
