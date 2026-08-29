import unittest

from src.utils import (
    canonicalize_url,
    infer_page_kind,
    is_blog_post_url,
    is_case_study_url,
    is_category_url,
    is_denied_website_url,
    is_pagination_url,
    is_website_hub_url,
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

    def test_blog_urls_still_drop_query(self):
        url = canonicalize_url("https://arnifi.com/blog/vat-guide?utm_source=x")
        self.assertEqual(url, "https://arnifi.com/blog/vat-guide/")

    def test_product_listing_keeps_jurisdiction_query(self):
        url = canonicalize_url(
            "https://arnifi.com/product-listing?countries=1&productType=licence&utm_source=x"
        )
        self.assertIn("countries=1", url)
        self.assertIn("productType=licence", url)
        self.assertNotIn("utm_source", url)

    def test_llms_txt_has_no_trailing_slash(self):
        url = canonicalize_url("https://arnifi.com/llms.txt")
        self.assertEqual(url, "https://arnifi.com/llms.txt")

    def test_website_hubs_and_case_studies(self):
        self.assertTrue(is_website_hub_url("https://arnifi.com/services/visa-service/"))
        self.assertTrue(is_case_study_url(
            "https://arnifi.com/case-studies/can-logistics-companies-scale-operations-in-dmcc/"
        ))
        self.assertFalse(is_case_study_url("https://arnifi.com/case-studies/industries/logistics/"))
        self.assertEqual(infer_page_kind("https://arnifi.com/pricing-master-list/"), "pricing")
        self.assertTrue(is_denied_website_url("https://arnifi.com/cost-calculator/"))
        self.assertTrue(is_denied_website_url("https://arnifi.com/blog/some-post/"))


if __name__ == "__main__":
    unittest.main()
