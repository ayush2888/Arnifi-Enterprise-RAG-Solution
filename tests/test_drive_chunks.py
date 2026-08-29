"""Tests for Drive extracted JSON -> Chunk mapping."""

from __future__ import annotations

from app.services.ingestion.drive import drive_file_url, extracted_to_chunks


def test_extracted_to_chunks_basic() -> None:
    payload = {
        "file_id": "abc123",
        "name": "Pricing Master Sheet - HR Compliance.csv",
        "folder_path": "Pricing Master Sheet",
        "kind": "csv",
        "row_texts": [
            "Service Code: PM1121\nService: HR & Payroll Essentials\nRegion: UAE\nPrice: 299",
            "Service Code: PM1122\nService: Other\nRegion: UAE\nPrice: 100",
        ],
        "text": "placeholder",
        "modified_time": "2026-07-09T10:11:53.000Z",
    }
    chunks = extracted_to_chunks(payload, max_tokens=400, overlap_tokens=40)
    assert len(chunks) == 2
    chunk = chunks[0]
    assert chunk.source_type == "drive"
    assert chunk.source_domain == "drive.google.com"
    assert chunk.source_url == drive_file_url("abc123")
    assert chunk.doc_title.startswith("Pricing Master Sheet")
    assert "Google Drive" in chunk.embed_text
    assert "Service Code: PM1121" in chunk.chunk_text
    assert chunk.metadata()["source_type"] == "drive"


def test_csv_row_chunk_includes_iqama_fee_row() -> None:
    payload = {
        "file_id": "ksa1",
        "name": "Pricing Master Sheet - KSA Services.csv",
        "folder_path": "Pricing Master Sheet",
        "kind": "csv",
        "mime_type": "text/csv",
        "row_texts": [
            "Service Code: PM1130\nService: Other Fee\nAuthority fees: 100\nArnifi fees: 50\nCurrency: SAR",
            "Service Code: PM1134\nService: IQAMA Medical\nAuthority fees: 550\nArnifi fees: 450\nCurrency: SAR",
            "Service Code: PM1135\nService: Something Else\nAuthority fees: 10\nArnifi fees: 5\nCurrency: SAR",
        ],
        "text": "joined",
    }
    chunks = extracted_to_chunks(payload)
    assert len(chunks) == 3
    iqama = next(c for c in chunks if "IQAMA Medical" in c.chunk_text)
    assert "Authority fees: 550" in iqama.chunk_text
    assert "Arnifi fees: 450" in iqama.chunk_text
    assert "Service Code: PM1134" in iqama.embed_text


def test_pdf_still_uses_token_windows() -> None:
    payload = {
        "file_id": "pdf1",
        "name": "handbook.pdf",
        "folder_path": "Funds",
        "kind": "pdf",
        "mime_type": "application/pdf",
        "text": ("Fee schedule paragraph. " * 80),
    }
    chunks = extracted_to_chunks(payload, max_tokens=50, overlap_tokens=10)
    assert len(chunks) >= 2
    assert all(c.source_type == "drive" for c in chunks)


def test_extracted_to_chunks_empty_text() -> None:
    assert extracted_to_chunks({"file_id": "x", "name": "empty.csv", "text": "  "}) == []
    assert (
        extracted_to_chunks(
            {"file_id": "x", "name": "empty.csv", "kind": "csv", "row_texts": []}
        )
        == []
    )
