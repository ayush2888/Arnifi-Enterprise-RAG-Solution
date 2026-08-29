"""Unit tests for Drive discover report formatting (no live Drive API)."""

from __future__ import annotations

from app.services.drive.discover import format_discover_text


def test_format_discover_text_includes_counts() -> None:
    report = {
        "root_folder_id": "root123",
        "root_name": "Arnifi Drive",
        "summary": {
            "total_items": 5,
            "folders": 2,
            "files": 3,
            "supported_for_ingest": 2,
            "unsupported_skipped_by_ingest": 1,
        },
        "by_extension": {".pdf": 2, ".csv": 1, ".jpg": 1},
        "by_mime_type": {},
        "folders": [
            {
                "folder": "Pricing Master Sheet ",
                "files": 2,
                "supported_pdf_csv": 2,
                "unsupported": 0,
                "by_ext": {".csv": 2},
            }
        ],
        "supported_files": [],
        "unsupported_files": [],
    }
    text = format_discover_text(report)
    assert "ARNIFI DRIVE DISCOVERY REPORT" in text
    assert "Arnifi Drive" in text
    assert "Folders discovered" in text
    assert "Pricing Master Sheet" in text
    assert ".pdf" in text
    assert "PER FOLDER BREAKDOWN" in text
