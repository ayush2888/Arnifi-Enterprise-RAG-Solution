"""Unit tests for Drive PDF/CSV/XLSX text extractors (no network)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.services.ingestion.extractors import (
    extract_csv_rows,
    extract_csv_text,
    extract_drive_content,
    extract_text,
    extract_xlsx_rows,
    is_supported_drive_file,
)


def test_is_supported_drive_file() -> None:
    assert is_supported_drive_file("application/pdf", "a.pdf")
    assert is_supported_drive_file("text/csv", "a.csv")
    assert is_supported_drive_file("application/octet-stream", "notes.csv")
    assert is_supported_drive_file(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "fees.xlsx",
    )
    assert is_supported_drive_file("application/octet-stream", "book.xlsx")
    assert not is_supported_drive_file("application/vnd.google-apps.document", "Doc")
    assert not is_supported_drive_file("image/png", "scan.png")


def test_extract_csv_rows_header_aware(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text(
        "Service Code,Service,Authority fees,Arnifi fees,Currency\n"
        "PM1134,IQAMA Medical,550,450,SAR\n"
        "PM1135,Other,10,5,SAR\n",
        encoding="utf-8",
    )
    rows = extract_csv_rows(path)
    assert len(rows) == 2
    assert "Service Code: PM1134" in rows[0]
    assert "Service: IQAMA Medical" in rows[0]
    assert "Authority fees: 550" in rows[0]


def test_extract_csv_text(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text("Service,Price\nVisa,100\nSetup,200\n", encoding="utf-8")
    text = extract_csv_text(path)
    assert "Service: Visa" in text
    assert "Price: 100" in text


def test_extract_text_dispatcher_csv(tmp_path: Path) -> None:
    path = tmp_path / "rates.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    text = extract_text(path, mime_type="text/csv", name="rates.csv")
    assert "a: 1" in text
    assert "b: 2" in text


def test_extract_xlsx_rows(tmp_path: Path) -> None:
    path = tmp_path / "fees.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "KSA"
    ws.append(["Service Code", "Service", "Authority fees"])
    ws.append(["PM1134", "IQAMA Medical", 550])
    ws.append(["PM1135", "Other", 10])
    wb.save(path)
    wb.close()

    rows = extract_xlsx_rows(path)
    assert len(rows) == 2
    assert rows[0].startswith("Sheet: KSA")
    assert "Service Code: PM1134" in rows[0]
    assert "IQAMA Medical" in rows[0]

    content = extract_drive_content(path, name="fees.xlsx")
    assert content["kind"] == "xlsx"
    assert content["row_texts"] is not None
    assert len(content["row_texts"]) == 2
