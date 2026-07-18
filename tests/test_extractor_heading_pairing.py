import unittest

from src.ingest import _pair_headings_to_sections
from src.models import Block


class ExtractorHeadingTests(unittest.TestCase):
    def test_heading_pairing_hierarchy(self):
        blocks = [
            Block(index=0, kind="h2", text="Introduction"),
            Block(index=1, kind="p", text="Intro paragraph."),
            Block(index=2, kind="h3", text="FAQ One"),
            Block(index=3, kind="p", text="FAQ answer."),
        ]
        sections = _pair_headings_to_sections(blocks, "https://arnifi.com/blog/sample/")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].heading_path, ["Introduction"])
        self.assertIn("Intro paragraph", sections[0].text)
        self.assertEqual(sections[1].heading_path, ["Introduction", "FAQ One"])
        self.assertIn("FAQ answer", sections[1].text)

    def test_introduction_fallback(self):
        blocks = [Block(index=0, kind="p", text="Content before any heading.")]
        sections = _pair_headings_to_sections(blocks, "https://arnifi.com/blog/sample/")
        self.assertEqual(sections[0].heading_path, ["Introduction"])


if __name__ == "__main__":
    unittest.main()
