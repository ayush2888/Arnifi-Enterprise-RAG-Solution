import unittest

from src.models import RetrievedChunk
from src.query import diversify_chunks


class RetrieverTests(unittest.TestCase):
    def test_diversify_limits_per_source(self):
        matches = [
            RetrievedChunk(
                chunk_id="a1",
                score=0.9,
                source_url="https://arnifi.com/blog/a/",
                doc_title="A",
                heading_path="Intro",
                chunk_text="text a1",
            ),
            RetrievedChunk(
                chunk_id="a2",
                score=0.85,
                source_url="https://arnifi.com/blog/a/",
                doc_title="A",
                heading_path="FAQ",
                chunk_text="text a2",
            ),
            RetrievedChunk(
                chunk_id="b1",
                score=0.8,
                source_url="https://arnifi.com/blog/b/",
                doc_title="B",
                heading_path="Intro",
                chunk_text="text b1",
            ),
            RetrievedChunk(
                chunk_id="c1",
                score=0.75,
                source_url="https://arnifi.com/blog/c/",
                doc_title="C",
                heading_path="Intro",
                chunk_text="text c1",
            ),
        ]
        selected = diversify_chunks(
            matches,
            max_chunks_returned=5,
            max_chunks_per_source_url=2,
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(selected[0].chunk_id, "a1")
        self.assertEqual(selected[1].chunk_id, "a2")
        source_urls = {chunk.source_url for chunk in selected}
        self.assertEqual(len(source_urls), 3)


if __name__ == "__main__":
    unittest.main()
