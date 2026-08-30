"""Tests for Business Guides gate + Press/Events/Case Studies extract/ingest."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from app.models.schemas import Document, Section
from app.services.ingestion.announcement_content import (
    discover_case_studies,
    extract_case_study_from_api,
    extract_event,
    extract_press_release,
    strip_truncation_artifacts,
)
from app.services.ingestion.company_fund_catalog import (
    discover_guides_catalog,
    map_guide_listing_row,
)
from app.services.ingestion.pipeline import chunk_document
from app.utils.helpers import infer_page_kind, is_event_url, is_press_release_url


PRESS_HTML = """
<html><body>
<script>self.__next_f.push([1,"{\\"id\\":28,\\"type\\":\\"Press Release\\",\\"title\\":\\"Arnifi launches AI platform\\",\\"date\\":\\"2026-06-09\\",\\"startTime\\":null,\\"endTime\\":null,\\"location\\":\\"The Hindu Businessline\\",\\"description\\":\\"$24\\",\\"slug\\":\\"arnifi-launches-ai-platform\\",\\"isUpcomingEvents\\":false,\\"publishedAt\\":\\"2026-07-08T09:10:43.889Z\\",\\"articleLink\\":\\"https://example.com/article\\"}"])</script>
<h1>Arnifi launches AI platform</h1>
<p>The Hindu Businessline</p>
<p>Arnifi's launch of its Business Banking and Payments Setup Assistance Platform was featured by Business Standard, recognizing the company's efforts to simplify cross-border banking for founders.</p>
<p>The feature highlights how the platform brings together leading traditional banks, digital banks, and payment providers into a single experience for global expansion.</p>
<p>View Article</p>
</body></html>
"""

EVENT_HTML = """
<html><body>
<script>self.__next_f.push([1,"{\\"id\\":18,\\"type\\":\\"Archived Events\\",\\"title\\":\\"Business Setup Summit\\",\\"date\\":\\"2024-02-21\\",\\"startTime\\":\\"12:00:00\\",\\"endTime\\":\\"17:00:00\\",\\"location\\":\\"Bengaluru\\",\\"description\\":\\"Arnifi hosted an exclusive event in Bengaluru focused on DMCC Free Zone setup for founders.\\n\\nAttendees learned licensing steps and timelines.\\n\\nNetworking followed the sessions.\\",\\"slug\\":\\"business-setup-summit\\",\\"isUpcomingEvents\\":false,\\"publishedAt\\":\\"2026-03-27T12:09:13.602Z\\",\\"articleLink\\":null}"])</script>
<h1>Business Setup Summit</h1>
</body></html>
"""

UPCOMING_EVENT_HTML = """
<html><body>
<script>self.__next_f.push([1,"{\\"id\\":99,\\"type\\":\\"Upcoming Events\\",\\"title\\":\\"Future Meetup\\",\\"date\\":\\"2027-01-01\\",\\"startTime\\":null,\\"endTime\\":null,\\"location\\":\\"Dubai\\",\\"description\\":\\"\\",\\"slug\\":\\"future-meetup\\",\\"isUpcomingEvents\\":true,\\"publishedAt\\":\\"2026-03-27T12:09:13.602Z\\"}"])</script>
<p>Notify Me</p>
</body></html>
"""


class TestGuidesCatalog(unittest.TestCase):
    def test_map_guide_row(self) -> None:
        row = {
            "id": 501,
            "slug": "uae-setup-guide",
            "bottomTitle": "UAE Setup Guide",
            "startingPrice": 0,
            "currency": "USD",
            "badgeLabel": "UAE",
        }
        card = map_guide_listing_row(row)
        self.assertEqual(card["product_type"], "guide")
        self.assertEqual(card["discovered_via"], "business-guides")
        self.assertIn("product-insights", card["detail_url"])

    def test_guides_completeness_gate(self) -> None:
        client = MagicMock()
        client.list_micro_services.return_value = [
            {"id": 1, "slug": "a", "bottomTitle": "A"},
            {"id": 2, "slug": "b", "bottomTitle": "B"},
            {"id": 3, "slug": "c", "bottomTitle": "C"},
            {"id": 4, "slug": "d", "bottomTitle": "D"},
        ]
        with patch(
            "app.services.ingestion.company_fund_catalog.fetch_live_stated_total",
            return_value=4,
        ):
            cat = discover_guides_catalog(client)
        self.assertTrue(cat["complete"])
        self.assertEqual(cat["discovered"], 4)
        self.assertEqual(cat["live_stated_total"], 4)

        with patch(
            "app.services.ingestion.company_fund_catalog.fetch_live_stated_total",
            return_value=5,
        ):
            cat2 = discover_guides_catalog(client)
        self.assertFalse(cat2["complete"])


class TestAnnouncementExtract(unittest.TestCase):
    def test_strip_read_more(self) -> None:
        text = "Arnifi expands globally. Read More"
        self.assertNotIn("Read More", strip_truncation_artifacts(text))

    def test_extract_press(self) -> None:
        url = "https://arnifi.com/announcement/press-releases/arnifi-launches-ai-platform"
        doc, meta = extract_press_release(PRESS_HTML, url)
        self.assertEqual(meta["content_type"], "press_release")
        self.assertEqual(meta["source_publication"], "The Hindu Businessline")
        self.assertEqual(meta["publish_date"], "2026-06-09")
        self.assertTrue(doc.sections)
        self.assertNotIn("Read More", meta["full_body_text"])
        self.assertIn("Business Banking", meta["full_body_text"])

    def test_extract_event(self) -> None:
        url = "https://arnifi.com/announcement/events/business-setup-summit"
        doc, meta = extract_event(EVENT_HTML, url)
        self.assertEqual(meta["content_type"], "event")
        self.assertEqual(meta["event_date"], "2024-02-21")
        self.assertEqual(meta["location"], "Bengaluru")
        self.assertIn("12:00:00", meta["event_time"] or "")
        self.assertTrue(doc.sections)

    def test_skip_notify_me_event(self) -> None:
        url = "https://arnifi.com/announcement/events/future-meetup"
        with self.assertRaises(RuntimeError):
            extract_event(UPCOMING_EVENT_HTML, url)

    def test_extract_case_study_sections(self) -> None:
        payload = {
            "title": "How firms expand in ADGM",
            "slug": "how-firms-expand",
            "readingTime": "10 min read",
            "publishedAt": "2026-07-10T10:16:40.748Z",
            "jurisdiction": {"name": "UAE"},
            "industry": {"name": "Financial Services"},
            "description": [
                {
                    "type": "heading",
                    "level": 2,
                    "children": [{"text": "Challenge", "type": "text"}],
                },
                {
                    "type": "paragraph",
                    "children": [
                        {
                            "text": "The client needed a dual-entity structure across ADGM and mainland UAE for holding and operations.",
                            "type": "text",
                        }
                    ],
                },
                {
                    "type": "heading",
                    "level": 2,
                    "children": [{"text": "Solution", "type": "text"}],
                },
                {
                    "type": "paragraph",
                    "children": [
                        {
                            "text": "Arnifi designed a compliant structure with clear licensing and banking pathways.",
                            "type": "text",
                        }
                    ],
                },
            ],
        }
        url = "https://arnifi.com/case-studies/how-firms-expand"
        doc, meta = extract_case_study_from_api(payload, url)
        self.assertEqual(meta["content_type"], "case_study")
        self.assertEqual(meta["chunk_mode"], "section")
        self.assertEqual(meta["jurisdiction"], "UAE")
        self.assertEqual(meta["industry"], "Financial Services")
        headings = [s.heading_text for s in doc.sections]
        self.assertIn("Challenge", headings)
        self.assertIn("Solution", headings)

    def test_case_study_paragraph_mode(self) -> None:
        payload = {
            "title": "Logistics in DMCC",
            "slug": "logistics-dmcc",
            "description": [
                {
                    "type": "paragraph",
                    "children": [
                        {
                            "text": "First paragraph about logistics scale-up across DMCC warehouses and customs processes.",
                            "type": "text",
                        }
                    ],
                },
                {
                    "type": "paragraph",
                    "children": [
                        {
                            "text": "Second paragraph covering outcomes for shipping partners and compliance teams.",
                            "type": "text",
                        }
                    ],
                },
            ],
        }
        doc, meta = extract_case_study_from_api(
            payload, "https://arnifi.com/case-studies/logistics-dmcc"
        )
        self.assertEqual(meta["chunk_mode"], "paragraph")
        self.assertTrue(doc.sections)


class TestIndustryDedupe(unittest.TestCase):
    def test_industry_urls_do_not_duplicate(self) -> None:
        client = MagicMock()
        client.list_case_studies.return_value = [
            {"slug": "alpha-case", "title": "Alpha", "industry": {"name": "Fin"}, "jurisdiction": {"name": "UAE"}},
            {"slug": "beta-case", "title": "Beta", "industry": {"name": "Log"}, "jurisdiction": {"name": "UAE"}},
        ]
        main_html = """
        <html><body>
          <a href="/case-studies/alpha-case">A</a>
          <a href="/case-studies/beta-case">B</a>
          <a href="/case-studies/industries/financial-services">Fin</a>
        </body></html>
        """
        industry_html = """
        <html><body>
          <a href="/case-studies/alpha-case">A</a>
          <a href="/case-studies/beta-case">B</a>
        </body></html>
        """

        def fake_fetch(url: str, *, timeout: int = 40) -> str:
            if "/industries/" in url:
                return industry_html
            return main_html

        with patch(
            "app.services.ingestion.announcement_content._fetch_html",
            side_effect=fake_fetch,
        ):
            cat = discover_case_studies(client)
        self.assertEqual(cat["confirmed_total"], 2)
        self.assertEqual(len(cat["detail_urls"]), 2)
        self.assertEqual(cat["industry_only_urls"], [])
        self.assertTrue(cat["complete"])


class TestContentTypeMetadata(unittest.TestCase):
    def test_chunk_carries_content_type(self) -> None:
        doc = Document(
            title="Press",
            source_url="https://arnifi.com/announcement/press-releases/x",
            sections=[
                Section(
                    section_id="body",
                    heading_path=["Body"],
                    heading_text="Body",
                    text="Press body text long enough for a retrieval chunk unit.",
                )
            ],
        )
        chunks = chunk_document(
            doc,
            page_kind="press_release",
            source_type="website",
            content_type="press_release",
            publish_date="2026-06-09",
            source_publication="The Hindu Businessline",
        )
        self.assertTrue(chunks)
        self.assertEqual(chunks[0].content_type, "press_release")
        meta = chunks[0].metadata()
        self.assertEqual(meta["content_type"], "press_release")
        self.assertEqual(meta["source_publication"], "The Hindu Businessline")

    def test_page_kind_helpers(self) -> None:
        press = "https://arnifi.com/announcement/press-releases/some-slug"
        event = "https://arnifi.com/announcement/events/some-slug"
        self.assertTrue(is_press_release_url(press))
        self.assertTrue(is_event_url(event))
        self.assertEqual(infer_page_kind(press), "press_release")
        self.assertEqual(infer_page_kind(event), "event")
        self.assertEqual(
            infer_page_kind("https://arnifi.com/announcement/press-releases"),
            "hub",
        )


if __name__ == "__main__":
    unittest.main()
