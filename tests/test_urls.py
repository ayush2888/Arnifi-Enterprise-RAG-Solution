import unittest

from src.utils import (
    canonicalize_url,
    is_blog_post_url,
    is_category_url,
    is_pagination_url,
    listing_base_url,
)


class UrlTests(unittest.TestCase):
    def test_canonicalize_trailing_slash(self):
        url = canonicalize_url("https://arnifi.com/blog/post")
        self.assertEqual(url, "https://arnifi.com/blog/post/")

    def test_blog_post_detection(self):
        self.assertTrue(is_blog_post_url("https://arnifi.com/blog/vat-tax-refund-malaysia-2026-step-by-step-guide/"))
        self.assertFalse(is_blog_post_url("https://arnifi.com/blog/category/business-in-malaysia/"))
        self.assertFalse(is_blog_post_url("https://arnifi.com/blog/"))

    def test_category_and_pagination(self):
        self.assertTrue(is_category_url("https://arnifi.com/blog/category/business-in-malaysia/"))
        self.assertTrue(is_pagination_url("https://arnifi.com/blog/category/business-in-malaysia/page/4/"))

    def test_listing_base_url(self):
        base = listing_base_url("https://arnifi.com/blog/category/business-in-malaysia/page/4/")
        self.assertEqual(base, "https://arnifi.com/blog/category/business-in-malaysia/")


if __name__ == "__main__":
    unittest.main()
