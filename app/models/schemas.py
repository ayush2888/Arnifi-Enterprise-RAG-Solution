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
        }
        optional = {
            "doc_author": self.doc_author,
            "doc_published_at": self.doc_published_at,
            "doc_category": self.doc_category,
            "doc_category_url": self.doc_category_url,
            "listing_url": self.listing_url,
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
