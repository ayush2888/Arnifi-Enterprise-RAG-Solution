"""
Website/blog scraper components.

Fetcher and ListingCrawler discover and download pages; embedding/upsert
happen in the ingestion layer so scrape logic stays free of AWS SDK calls.
"""

from app.services.ingestion.pipeline import (
    Fetcher,
    ListingCrawler,
    extract_links_from_listing,
)

__all__ = ["Fetcher", "ListingCrawler", "extract_links_from_listing"]
