"""Unit tests for sitemap loc parsing / blog URL classification."""
from app.services.ingestion.pipeline import _sitemap_locs
from app.utils.helpers import is_blog_post_url, is_category_url


def test_sitemap_locs_extracts_urls():
    xml = """<?xml version="1.0"?>
    <urlset>
      <url><loc>https://arnifi.com/blog/hello-world/</loc></url>
      <url><loc>https://arnifi.com/blog/category/news/</loc></url>
    </urlset>
    """
    locs = _sitemap_locs(xml)
    assert "https://arnifi.com/blog/hello-world/" in locs
    assert "https://arnifi.com/blog/category/news/" in locs


def test_mauritius_post_classified():
    url = "https://arnifi.com/blog/mauritius-fintech-sandbox-vaitos-tokenisation-2026-guide/"
    assert is_blog_post_url(url)
    assert not is_category_url(url)


def test_mauritius_category_classified():
    url = "https://arnifi.com/blog/category/business-incorporation-in-mauritius/"
    assert is_category_url(url)
    assert not is_blog_post_url(url)
