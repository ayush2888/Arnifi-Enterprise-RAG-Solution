"""
Data models shared across indexing and query pipelines.

Document / Section / Block — parsed blog content
Chunk — retrieval unit stored in Pinecone
RetrievedChunk / RAGResponse — query results
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Block(BaseModel):
    index: int
    kind: str
    text: str


class Section(BaseModel):
    section_id: str
    heading_path: list[str]
    heading_text: str
    text: str
    block_spans: list[int] = Field(default_factory=list)


class Document(BaseModel):
    title: str
    author: str | None = None
    published_at: str | None = None
    source_url: str
    source_domain: str = "arnifi.com"
    category_name: str | None = None
    category_url: str | None = None
    sections: list[Section] = Field(default_factory=list)


class Chunk(BaseModel):
    chunk_id: str
    source_url: str
    source_domain: str
    doc_title: str
    doc_author: str | None = None
    doc_published_at: str | None = None
    doc_category: str | None = None
    doc_category_url: str | None = None
    listing_url: str | None = None
    section_id: str
    heading_path: str
    heading_text: str
    chunk_index: int
    chunk_text: str
    embed_text: str
    chunk_char_len: int
    crawl_ts: str
    content_sha1: str
    source_type: str = "blog"
    page_kind: str | None = None
    whatsapp_invite_link: str | None = None
    # Service-package hierarchy (filterable in Pinecone)
    catalog_service: str | None = None
    catalog_package: str | None = None
    catalog_section: str | None = None
    # Company / Fund catalog lineage
    product_type: str | None = None
    discovered_via: str | None = None
    # Press / Events / Case Studies
    content_type: str | None = None
    publish_date: str | None = None
    source_publication: str | None = None
    event_date: str | None = None
    event_time: str | None = None
    location: str | None = None
    partners: str | None = None  # JSON array string for Pinecone metadata
    jurisdiction: str | None = None
    industry: str | None = None
    read_time: str | None = None
    # Team / leadership
    person_name: str | None = None
    related_person: str | None = None
    source_note: str | None = None

    def metadata(self) -> dict:
        meta = {
            "source_url": self.source_url,
            "source_domain": self.source_domain,
            "doc_title": self.doc_title,
            "section_id": self.section_id,
            "heading_path": self.heading_path,
            "heading_text": self.heading_text,
            "chunk_index": self.chunk_index,
            "chunk_text": self.chunk_text,
            "chunk_char_len": self.chunk_char_len,
            "crawl_ts": self.crawl_ts,
            "content_sha1": self.content_sha1,
            "source_type": self.source_type,
        }
        optional = {
            "doc_author": self.doc_author,
            "doc_published_at": self.doc_published_at,
            "doc_category": self.doc_category,
            "doc_category_url": self.doc_category_url,
            "listing_url": self.listing_url,
            "page_kind": self.page_kind,
            "whatsapp_invite_link": self.whatsapp_invite_link,
            "catalog_service": self.catalog_service,
            "catalog_package": self.catalog_package,
            "catalog_section": self.catalog_section,
            "product_type": self.product_type,
            "discovered_via": self.discovered_via,
            "content_type": self.content_type,
            "publish_date": self.publish_date,
            "source_publication": self.source_publication,
            "event_date": self.event_date,
            "event_time": self.event_time,
            "location": self.location,
            "partners": self.partners,
            "jurisdiction": self.jurisdiction,
            "industry": self.industry,
            "read_time": self.read_time,
            "person_name": self.person_name,
            "related_person": self.related_person,
            "source_note": self.source_note,
        }
        meta.update({k: v for k, v in optional.items() if v})
        return meta


class RetrievedChunk(BaseModel):
    chunk_id: str
    score: float
    source_url: str
    doc_title: str
    heading_path: str
    chunk_text: str
    metadata: dict = Field(default_factory=dict)


class RAGResponse(BaseModel):
    answer: str
    sources: list[dict]
