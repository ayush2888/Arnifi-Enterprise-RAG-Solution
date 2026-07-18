import unittest

from src.ingest import chunk_document
from src.models import Document, Section


class ChunkMetadataTests(unittest.TestCase):
    def test_metadata_contains_source_url(self):
        doc = Document(
            title="Malaysia VAT",
            source_url="https://arnifi.com/blog/vat-tax-refund-malaysia-2026-step-by-step-guide/",
            category_name="Business in Malaysia",
            sections=[
                Section(
                    section_id="s1",
                    heading_path=["Introduction"],
                    heading_text="Introduction",
                    text="Malaysia uses SST rather than VAT for most domestic transactions.",
                )
            ],
        )
        chunks = chunk_document(doc, embedding_model="text-embedding-3-small")
        meta = chunks[0].metadata()
        required = {
            "source_url",
            "source_domain",
            "doc_title",
            "section_id",
            "heading_path",
            "heading_text",
            "chunk_index",
            "chunk_text",
            "chunk_char_len",
            "crawl_ts",
            "content_sha1",
        }
        self.assertTrue(required.issubset(meta.keys()))
        self.assertEqual(meta["source_url"], doc.source_url)
        self.assertIn("Title: Malaysia VAT", chunks[0].embed_text)


if __name__ == "__main__":
    unittest.main()
