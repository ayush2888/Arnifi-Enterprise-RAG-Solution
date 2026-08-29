"""
Lexical rescue for Pricing Master identity (PM#### codes + exact Title phrases).

Titan often ranks near-twin service names / rare IDs wrong. When the user types
PM1122 or a full service title, inject the matching pricing row so Nova sees
the correct fee.

Index sources (first hit wins):
  1. Bundled JSON shipped with the app (Lambda-safe; no Drive extracts needed)
  2. Local Drive extract scan under data/artifacts/drive/extracted (dev machine)
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.models.schemas import RetrievedChunk
from app.services.ingestion.drive import drive_file_url
from app.services.retrieval.fee_focus import (
    chunk_matches_service_codes,
    chunk_title_matches_question,
    extract_service_codes,
    is_fee_or_payer_query,
    is_pricing_row_chunk,
    pricing_row_title,
)
from app.utils.helpers import get_logger

logger = get_logger(__name__)

_CODE_RE = re.compile(r"\bPM\s*-?\s*(\d{3,})\b", re.IGNORECASE)

INDEX_VERSION = 1

# code -> list of RetrievedChunk templates
_INDEX: dict[str, list[RetrievedChunk]] | None = None
# rows that have a Title: field (for phrase matching)
_TITLE_ROWS: list[RetrievedChunk] | None = None
_INDEX_KEY: str | None = None


def _codes_in_text(text: str) -> set[str]:
    return {f"PM{m.group(1)}" for m in _CODE_RE.finditer(text or "")}


def _drive_chunk_id(file_id: str, chunk_index: int, embedding_model: str) -> str:
    return hashlib.sha1(
        f"drive|{file_id}|{chunk_index}|{embedding_model}".encode()
    ).hexdigest()


def _extracted_dir(artifacts_dir: str | Path) -> Path:
    return Path(artifacts_dir) / "drive" / "extracted"


def _copy_hit(chunk: RetrievedChunk) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        score=1.0,
        source_url=chunk.source_url,
        doc_title=chunk.doc_title,
        heading_path=chunk.heading_path,
        chunk_text=chunk.chunk_text,
        metadata=dict(chunk.metadata or {}),
    )


def _chunk_from_row(row: dict[str, Any]) -> RetrievedChunk:
    codes = [str(c) for c in (row.get("service_codes") or []) if c]
    title = str(row.get("row_title") or "")
    return RetrievedChunk(
        chunk_id=str(row.get("chunk_id") or ""),
        score=1.0,
        source_url=str(row.get("source_url") or ""),
        doc_title=str(row.get("doc_title") or ""),
        heading_path=str(row.get("heading_path") or ""),
        chunk_text=str(row.get("chunk_text") or ""),
        metadata={
            "source_type": "drive",
            "service_codes": codes,
            "row_title": title,
            "lexical_rescue": True,
        },
    )


def _row_from_chunk(chunk: RetrievedChunk) -> dict[str, Any]:
    meta = chunk.metadata or {}
    codes = meta.get("service_codes") or sorted(_codes_in_text(chunk.chunk_text))
    title = meta.get("row_title") or pricing_row_title(chunk.chunk_text)
    return {
        "chunk_id": chunk.chunk_id,
        "source_url": chunk.source_url,
        "doc_title": chunk.doc_title,
        "heading_path": chunk.heading_path,
        "chunk_text": chunk.chunk_text,
        "service_codes": list(codes),
        "row_title": title,
    }


def _install_index(
    index: dict[str, list[RetrievedChunk]],
    title_rows: list[RetrievedChunk],
    key: str,
) -> dict[str, list[RetrievedChunk]]:
    global _INDEX, _TITLE_ROWS, _INDEX_KEY
    _INDEX = index
    _TITLE_ROWS = title_rows
    _INDEX_KEY = key
    return index


def _index_from_rows(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[RetrievedChunk]], list[RetrievedChunk]]:
    index: dict[str, list[RetrievedChunk]] = {}
    title_rows: list[RetrievedChunk] = []
    seen_ids: set[str] = set()
    for row in rows:
        chunk = _chunk_from_row(row)
        if not chunk.chunk_text.strip():
            continue
        # Same physical row may map to multiple codes; keep one object per id
        # in title_rows, but allow multi-code index entries.
        for code in chunk.metadata.get("service_codes") or []:
            index.setdefault(str(code), []).append(chunk)
        title = (chunk.metadata or {}).get("row_title") or ""
        if title and chunk.chunk_id not in seen_ids:
            seen_ids.add(chunk.chunk_id)
            title_rows.append(chunk)
    return index, title_rows


def load_pricing_lexical_index(
    path: str | Path,
) -> tuple[dict[str, list[RetrievedChunk]], list[RetrievedChunk]]:
    """Load a previously exported bundled index (Lambda-safe)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"Invalid pricing lexical index (missing rows): {path}")
    return _index_from_rows(rows)


def collect_pricing_rows_from_extracts(
    artifacts_dir: str | Path,
    *,
    embedding_model: str = "amazon.titan-embed-text-v2:0",
) -> list[dict[str, Any]]:
    """
    Scan Drive extract JSON and return compact pricing-row dicts.

    Safe no-op (empty list) if the folder is missing.
    """
    folder = _extracted_dir(artifacts_dir)
    if not folder.is_dir():
        return []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Skip extract %s: %s", path.name, exc)
            continue

        name = str(payload.get("name") or path.stem)
        row_texts = payload.get("row_texts")
        if not isinstance(row_texts, list) or not row_texts:
            continue
        file_id = str(payload.get("file_id") or path.stem)
        folder_path = str(payload.get("folder_path") or "").strip()
        source_url = drive_file_url(file_id) if file_id else ""

        for idx, row in enumerate(row_texts):
            text = str(row or "").strip()
            if not text:
                continue
            codes = sorted(_codes_in_text(text))
            title = pricing_row_title(text)
            if not codes and not title:
                continue
            chunk_id = _drive_chunk_id(file_id, idx, embedding_model)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            rows.append(
                {
                    "chunk_id": chunk_id,
                    "source_url": source_url,
                    "doc_title": name,
                    "heading_path": folder_path or "Document",
                    "chunk_text": text,
                    "service_codes": codes,
                    "row_title": title,
                }
            )
    return rows


def export_pricing_lexical_index(
    artifacts_dir: str | Path,
    out_path: str | Path,
    *,
    embedding_model: str = "amazon.titan-embed-text-v2:0",
) -> dict[str, Any]:
    """
    Build a compact bundled index from local extracts and write JSON.

    Ship this file in the Lambda image so rescue works without /tmp extracts.
    """
    rows = collect_pricing_rows_from_extracts(
        artifacts_dir, embedding_model=embedding_model
    )
    codes = sorted(
        {c for row in rows for c in (row.get("service_codes") or []) if c}
    )
    titled = sum(1 for row in rows if row.get("row_title"))
    payload = {
        "version": INDEX_VERSION,
        "embedding_model": embedding_model,
        "code_count": len(codes),
        "row_count": len(rows),
        "titled_row_count": titled,
        "rows": rows,
    }
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Exported pricing lexical index: %d codes, %d rows (%d titled) → %s",
        len(codes),
        len(rows),
        titled,
        dest,
    )
    return {
        "path": str(dest),
        "code_count": len(codes),
        "row_count": len(rows),
        "titled_row_count": titled,
        "bytes": dest.stat().st_size,
    }


def reset_service_code_index_cache() -> None:
    """Clear in-process cache (tests / after rebuild)."""
    global _INDEX, _TITLE_ROWS, _INDEX_KEY
    _INDEX = None
    _TITLE_ROWS = None
    _INDEX_KEY = None


def build_service_code_index(
    artifacts_dir: str | Path,
    *,
    embedding_model: str = "amazon.titan-embed-text-v2:0",
    bundled_index_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, list[RetrievedChunk]]:
    """
    Load PM#### / Title index from bundled JSON or local Drive extracts.

    Preference order:
      1. bundled_index_path if the file exists (Lambda / CI)
      2. scan artifacts_dir/drive/extracted (local sync)
      3. empty index (log warning)
    """
    bundled = Path(bundled_index_path) if bundled_index_path else None
    art_root = str(Path(artifacts_dir).resolve())
    key = f"bundled:{bundled.resolve()}" if bundled and bundled.is_file() else f"extracts:{art_root}"

    if _INDEX is not None and _INDEX_KEY == key and not force:
        return _INDEX

    if bundled and bundled.is_file():
        try:
            index, title_rows = load_pricing_lexical_index(bundled)
            logger.info(
                "Pricing lexical index (bundled): %d codes, %d titled rows from %s",
                len(index),
                len(title_rows),
                bundled,
            )
            return _install_index(index, title_rows, key)
        except Exception as exc:
            logger.warning(
                "Failed to load bundled pricing index %s (%s); trying extracts",
                bundled,
                exc,
            )

    rows = collect_pricing_rows_from_extracts(
        artifacts_dir, embedding_model=embedding_model
    )
    if not rows:
        folder = _extracted_dir(artifacts_dir)
        logger.warning(
            "Service-code index empty (no bundled file, missing/empty extracts at %s)",
            folder,
        )
        return _install_index({}, [], key)

    index, title_rows = _index_from_rows(rows)
    logger.info(
        "Pricing lexical index (extracts): %d codes, %d titled rows from %s",
        len(index),
        len(title_rows),
        _extracted_dir(artifacts_dir),
    )
    return _install_index(index, title_rows, key)


def lookup_service_code_chunks(
    question: str,
    *,
    artifacts_dir: str | Path,
    embedding_model: str = "amazon.titan-embed-text-v2:0",
    bundled_index_path: str | Path | None = None,
) -> list[RetrievedChunk]:
    """Return local pricing rows that match PM#### codes in the question."""
    codes = extract_service_codes(question)
    if not codes:
        return []
    index = build_service_code_index(
        artifacts_dir,
        embedding_model=embedding_model,
        bundled_index_path=bundled_index_path,
    )
    hits: list[RetrievedChunk] = []
    seen: set[str] = set()
    for code in sorted(codes):
        for chunk in index.get(code, []):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            hits.append(_copy_hit(chunk))
    return hits


def lookup_title_chunks(
    question: str,
    *,
    artifacts_dir: str | Path,
    embedding_model: str = "amazon.titan-embed-text-v2:0",
    bundled_index_path: str | Path | None = None,
) -> list[RetrievedChunk]:
    """
    Return local pricing rows whose Title: is a contiguous phrase in the question.

    Longest title first (most specific service name).
    """
    build_service_code_index(
        artifacts_dir,
        embedding_model=embedding_model,
        bundled_index_path=bundled_index_path,
    )
    rows = _TITLE_ROWS or []
    hits = [
        _copy_hit(chunk)
        for chunk in rows
        if chunk_title_matches_question(question, chunk)
    ]
    hits.sort(key=lambda c: -len(pricing_row_title(c)))
    # Dedupe by chunk_id (same row may appear once)
    seen: set[str] = set()
    unique: list[RetrievedChunk] = []
    for chunk in hits:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        unique.append(chunk)
    return unique


def merge_lexical_service_code_hits(
    question: str,
    vector_matches: list[RetrievedChunk],
    lexical_hits: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """
    Prepend lexical PM#### hits, dedupe, and for fee+code queries keep only
    exact matching rows so Nova cannot cite a neighbor fee.
    """
    codes = extract_service_codes(question)
    if not codes:
        return vector_matches

    merged: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk in list(lexical_hits) + list(vector_matches):
        key = chunk.chunk_id or f"{chunk.source_url}|{(chunk.chunk_text or '')[:120]}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(chunk)

    exact = [m for m in merged if chunk_matches_service_codes(m, codes)]
    if exact and is_fee_or_payer_query(question):
        return exact

    if exact:
        others = [
            m
            for m in merged
            if not chunk_matches_service_codes(m, codes)
            and not is_pricing_row_chunk(m)
        ]
        return exact + others

    return merged


def merge_lexical_title_hits(
    question: str,
    vector_matches: list[RetrievedChunk],
    lexical_hits: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """
    Prepend exact Title: phrase hits. For fee queries keep only title matches
    so a near-twin (699 SST) cannot displace the named service (6,869).
    """
    if not lexical_hits and not any(
        chunk_title_matches_question(question, m) for m in vector_matches
    ):
        return vector_matches

    merged: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk in list(lexical_hits) + list(vector_matches):
        key = chunk.chunk_id or f"{chunk.source_url}|{(chunk.chunk_text or '')[:120]}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(chunk)

    exact = [m for m in merged if chunk_title_matches_question(question, m)]
    if not exact:
        return merged
    exact.sort(key=lambda m: -len(pricing_row_title(m)))
    if is_fee_or_payer_query(question):
        return exact
    others = [
        m
        for m in merged
        if not chunk_title_matches_question(question, m)
        and not is_pricing_row_chunk(m)
    ]
    return exact + others
