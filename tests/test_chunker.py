import unittest

from src.ingest import chunk_document, count_tokens
from src.models import Document, Section


class ChunkerTests(unittest.TestCase):
    def test_small_section_single_chunk(self):
        doc = Document(
            title="Test",
            source_url="https://arnifi.com/blog/test/",
            sections=[
                Section(
                    section_id="s1",
                    heading_path=["Introduction"],
                    heading_text="Introduction",
                    text="Short section text.",
                )
            ],
        )
        chunks = chunk_document(doc, max_tokens=400, overlap_tokens=80)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Short section text", chunks[0].chunk_text)

    def test_large_section_splits(self):
        paragraph = "word " * 500
        doc = Document(
            title="Long",
            source_url="https://arnifi.com/blog/long/",
            sections=[
                Section(
                    section_id="s1",
                    heading_path=["Details"],
                    heading_text="Details",
                    text=paragraph,
                )
            ],
        )
        chunks = chunk_document(doc, max_tokens=100, overlap_tokens=20)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.chunk_id for c in chunks))


if __name__ == "__main__":
    unittest.main()
