"""
Google Drive sync + ingest.

Milestone 2: download + extract text to data/artifacts/drive/
Milestone 3: chunk extracted JSON -> Titan embed -> Pinecone upsert
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.models.schemas import Chunk
from app.services.drive.client import DriveClient, DriveItem
from app.services.ingestion.extractors import (
    extract_drive_content,
    is_supported_drive_file,
    resolve_extract_kind,
)
from app.services.ingestion.pipeline import _chunk_section_text, _get_encoder
from app.utils.helpers import get_logger

if TYPE_CHECKING:
    from app.config.settings import Settings

logger = get_logger(__name__)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip())
    return (cleaned or "file")[:120]


def drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def _apply_limit(items: list[Any], limit: int) -> list[Any]:
    """limit <= 0 means unlimited."""
    if limit <= 0:
        return list(items)
    return list(items)[:limit]


def _path_matches(path: str | None, needle: str | None) -> bool:
    if not needle:
        return True
    return needle.lower() in (path or "").lower()


def _build_chunk(
    *,
    file_id: str,
    title: str,
    folder_path: str | None,
    source_url: str,
    heading: str,
    crawl_ts: str,
    modified_time: Any,
    chunk_index: int,
    chunk_text: str,
    embedding_model: str,
) -> Chunk:
    embed_text = (
        f"Source: Google Drive\n"
        f"Folder: {heading}\n"
        f"Title: {title}\n"
        f"{chunk_text}"
    )
    chunk_id = hashlib.sha1(
        f"drive|{file_id}|{chunk_index}|{embedding_model}".encode()
    ).hexdigest()
    content_sha1 = hashlib.sha1(chunk_text.encode("utf-8")).hexdigest()
    return Chunk(
        chunk_id=chunk_id,
        source_url=source_url,
        source_domain="drive.google.com",
        doc_title=title,
        doc_published_at=modified_time,
        doc_category=folder_path or None,
        section_id=f"drive-{file_id}-{chunk_index}",
        heading_path=heading,
        heading_text=heading,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        embed_text=embed_text,
        chunk_char_len=len(chunk_text),
        crawl_ts=crawl_ts,
        content_sha1=content_sha1,
        source_type="drive",
    )


def extracted_to_chunks(
    payload: dict[str, Any],
    *,
    max_tokens: int = 400,
    overlap_tokens: int = 80,
    embedding_model: str = "amazon.titan-embed-text-v2:0",
    max_chunks_per_file: int = 500,
) -> list[Chunk]:
    """Turn one Drive extracted JSON payload into Chunk objects."""
    file_id = str(payload.get("file_id") or "")
    title = str(payload.get("name") or file_id or "Drive file")
    folder_path = str(payload.get("folder_path") or "").strip()
    source_url = drive_file_url(file_id) if file_id else str(payload.get("full_path") or title)
    heading = folder_path or "Document"
    crawl_ts = datetime.now(timezone.utc).isoformat()
    kind = str(payload.get("kind") or "").strip().lower() or resolve_extract_kind(
        str(payload.get("mime_type") or ""),
        title,
    )

    row_texts = payload.get("row_texts")
    if isinstance(row_texts, list) and row_texts:
        pieces = [str(r).strip() for r in row_texts if str(r).strip()]
    elif kind in {"csv", "xlsx"}:
        # Legacy flat extract: still window — prefer re-sync for row awareness.
        text = (payload.get("text") or "").strip()
        if not text:
            return []
        encoder = _get_encoder()
        pieces = _chunk_section_text(
            text,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            encoder=encoder,
        )
    else:
        text = (payload.get("text") or "").strip()
        if not text:
            return []
        encoder = _get_encoder()
        pieces = _chunk_section_text(
            text,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            encoder=encoder,
        )

    if not pieces:
        return []

    if max_chunks_per_file > 0 and len(pieces) > max_chunks_per_file:
        logger.warning(
            "Truncating chunks for %s from %d to %d (max_chunks_per_file)",
            title,
            len(pieces),
            max_chunks_per_file,
        )
        pieces = pieces[:max_chunks_per_file]

    return [
        _build_chunk(
            file_id=file_id,
            title=title,
            folder_path=folder_path or None,
            source_url=source_url,
            heading=heading,
            crawl_ts=crawl_ts,
            modified_time=payload.get("modified_time"),
            chunk_index=idx,
            chunk_text=chunk_text,
            embedding_model=embedding_model,
        )
        for idx, chunk_text in enumerate(pieces)
    ]


class DriveSync:
    """Download supported Drive files and extract text to local artifacts."""

    def __init__(
        self,
        client: DriveClient,
        artifacts_dir: str | Path,
    ) -> None:
        self.client = client
        base = Path(artifacts_dir)
        self.raw_dir = base / "drive" / "raw"
        self.extracted_dir = base / "drive" / "extracted"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)

    def sync(
        self,
        limit: int = 0,
        folder_id: str | None = None,
        path_contains: str | None = None,
    ) -> dict[str, Any]:
        files = self.client.list_files(folder_id)
        supported = [
            item
            for item in files
            if is_supported_drive_file(item.mime_type, item.name)
            and _path_matches(item.full_path, path_contains)
        ]
        selected = _apply_limit(supported, limit)

        processed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped_unsupported = len(files) - sum(
            1 for item in files if is_supported_drive_file(item.mime_type, item.name)
        )

        for item in selected:
            try:
                result = self._sync_one(item)
                processed.append(result)
            except Exception as exc:
                logger.error("Drive sync failed for %s: %s", item.full_path, exc)
                failed.append(
                    {
                        "path": item.full_path,
                        "file_id": item.file_id,
                        "error": str(exc),
                    }
                )

        return {
            "root_folder_id": folder_id or self.client.root_folder_id,
            "files_seen": len(files),
            "supported_pdf_csv": len(
                [i for i in files if is_supported_drive_file(i.mime_type, i.name)]
            ),
            "matched_path_filter": len(supported),
            "skipped_unsupported": skipped_unsupported,
            "path_contains": path_contains,
            "limit": limit,
            "processed": len(processed),
            "failed": len(failed),
            "items": processed,
            "errors": failed,
            "raw_dir": str(self.raw_dir),
            "extracted_dir": str(self.extracted_dir),
        }

    def _sync_one(self, item: DriveItem) -> dict[str, Any]:
        raw_name = f"{item.file_id}_{_safe_filename(item.name)}"
        raw_path = self.raw_dir / raw_name
        self.client.download_file(item.file_id, raw_path)

        content = extract_drive_content(
            raw_path, mime_type=item.mime_type, name=item.name
        )
        text = str(content.get("text") or "")
        row_texts = content.get("row_texts")
        payload = {
            "file_id": item.file_id,
            "name": item.name,
            "folder_path": item.folder_path,
            "full_path": item.full_path,
            "mime_type": item.mime_type,
            "kind": content.get("kind"),
            "modified_time": item.modified_time,
            "size": item.size,
            "md5_checksum": item.md5_checksum,
            "raw_path": str(raw_path),
            "char_len": len(text),
            "row_count": len(row_texts) if isinstance(row_texts, list) else None,
            "text_preview": text[:500],
            "text": text,
            "row_texts": row_texts,
        }
        extracted_path = self.extracted_dir / f"{item.file_id}.json"
        extracted_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "Extracted %s (%d chars, rows=%s) -> %s",
            item.full_path,
            len(text),
            payload["row_count"],
            extracted_path,
        )
        return {
            "path": item.full_path,
            "file_id": item.file_id,
            "mime_type": item.mime_type,
            "kind": content.get("kind"),
            "raw_path": str(raw_path),
            "extracted_path": str(extracted_path),
            "char_len": len(text),
            "row_count": payload["row_count"],
            "text_preview": text[:200],
        }


class DriveIndexer:
    """Chunk extracted Drive files and upsert into Pinecone."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.extracted_dir = Path(settings.artifacts.base_dir) / "drive" / "extracted"

    def ingest(
        self,
        limit: int = 0,
        dry_run: bool = False,
        path_contains: str | None = None,
        purge_before_ingest: bool = False,
        skip_file_ids: set[str] | None = None,
        only_file_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        paths = sorted(self.extracted_dir.glob("*.json"))
        if not paths:
            return {
                "extracted_dir": str(self.extracted_dir),
                "files_found": 0,
                "files_processed": 0,
                "chunks_prepared": 0,
                "chunks_upserted": 0,
                "dry_run": dry_run,
                "message": "No extracted JSON found. Run drive-sync first.",
            }

        skip_ids = {s.strip() for s in (skip_file_ids or set()) if s and s.strip()}
        only_ids = {s.strip() for s in (only_file_ids or set()) if s and s.strip()}
        payloads: list[tuple[Path, dict[str, Any]]] = []
        skipped_resume = 0
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.error("Failed reading %s: %s", path, exc)
                continue
            file_id = str(payload.get("file_id") or path.stem)
            if only_ids and file_id not in only_ids and path.stem not in only_ids:
                skipped_resume += 1
                continue
            if file_id in skip_ids or path.stem in skip_ids:
                skipped_resume += 1
                continue
            if not _path_matches(str(payload.get("full_path") or path.name), path_contains):
                continue
            payloads.append((path, payload))

        selected = _apply_limit(payloads, limit)
        max_tokens = int(self.settings.setting("chunk", "max_tokens", default=400))
        overlap = int(self.settings.setting("chunk", "overlap_tokens", default=80))
        max_chunks_per_file = int(
            self.settings.setting("chunk", "max_chunks_per_file", default=500)
        )
        embedding_model = str(
            self.settings.setting(
                "embedding",
                "model",
                default="amazon.titan-embed-text-v2:0",
            )
        )

        purged = 0
        if purge_before_ingest and not dry_run:
            store = self.settings.vectorstore
            if path_contains:
                urls = {
                    drive_file_url(str(p.get("file_id")))
                    for _, p in selected
                    if p.get("file_id")
                }
                for url in sorted(urls):
                    purged += store.delete_by_filter(
                        {
                            "source_type": {"$eq": "drive"},
                            "source_url": {"$eq": url},
                        }
                    )
            else:
                purged += store.delete_by_filter(
                    {"source_type": {"$eq": "drive"}}
                )

        chunks_prepared = 0
        upserted = 0
        file_stats: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for path, payload in selected:
            try:
                chunks = extracted_to_chunks(
                    payload,
                    max_tokens=max_tokens,
                    overlap_tokens=overlap,
                    embedding_model=embedding_model,
                    max_chunks_per_file=max_chunks_per_file,
                )
                chunks_prepared += len(chunks)
                if chunks and not dry_run:
                    vectors = self.settings.embedder.embed_texts(
                        [chunk.embed_text for chunk in chunks]
                    )
                    upserted += self.settings.vectorstore.upsert_chunks(
                        chunks, vectors
                    )
                file_stats.append(
                    {
                        "file": path.name,
                        "title": payload.get("name"),
                        "path": payload.get("full_path"),
                        "kind": payload.get("kind"),
                        "chunks": len(chunks),
                        "char_len": payload.get("char_len"),
                        "row_count": payload.get("row_count"),
                    }
                )
                logger.info(
                    "Ingested %s → %d chunks (running upserted=%d)",
                    path.name,
                    len(chunks),
                    upserted,
                )
            except Exception as exc:
                logger.error("Drive ingest failed for %s: %s", path, exc)
                failed.append({"file": path.name, "error": str(exc)})

        result: dict[str, Any] = {
            "extracted_dir": str(self.extracted_dir),
            "files_found": len(paths),
            "files_matched": len(payloads),
            "files_skipped_resume": skipped_resume,
            "files_processed": len(file_stats),
            "files_failed": len(failed),
            "chunks_prepared": chunks_prepared,
            "chunks_upserted": upserted,
            "purged_before_ingest": purged,
            "path_contains": path_contains,
            "limit": limit,
            "dry_run": dry_run,
            "files": file_stats,
            "errors": failed,
        }
        if not dry_run and upserted:
            try:
                result["index_stats"] = self.settings.vectorstore.describe_stats()
            except Exception as exc:
                result["index_stats_error"] = str(exc)
        return result
