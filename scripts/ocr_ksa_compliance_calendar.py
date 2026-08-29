"""
KSA Compliance Calendar — structured OCR prototype (one file first).

Problem: plain Tesseract dumps columns separately, so Particulars and Frequency
are no longer linked. This script:
  1. Renders KSA.pdf page → image
  2. OCR with word bounding boxes
  3. Rebuilds each table row
  4. Writes one-fact-per-row text (RAG-safe) + markdown table

Usage (from Arnifi-Enterprise-RAG-Solution/):

  python scripts/ocr_ksa_compliance_calendar.py
  python scripts/ocr_ksa_compliance_calendar.py --pdf path/to/KSA.pdf
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

DEFAULT_PDF = ROOT / (
    "data/artifacts/drive/raw/1s6-t8tOwzXRbc0sNwTHWcIsAOuqT1eef_KSA.pdf"
)
OUT_DIR = ROOT / "data/eval/reports"


def render_pdf_page(pdf_path: Path, *, page_index: int = 0, zoom: float = 2.0):
    """Turn one PDF page into a PIL image (pixels the OCR engine can read)."""
    import fitz  # PyMuPDF
    from PIL import Image

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    finally:
        doc.close()


def ocr_words(img):
    import pytesseract

    from app.utils.ocr import configure_tesseract
    from app.utils.table_ocr import words_from_tesseract_data

    configure_tesseract()
    data = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT)
    return words_from_tesseract_data(data)


def structure_ksa_calendar(words, *, column_split_x: int = 650, min_top: int = 360):
    """
    KSA calendar layout (at 2x render):
      - Title lives above ~y=360
      - Left column = Particulars (and notes)
      - Right column = Frequency (and notes)
      - Vertical split ≈ x=650
    """
    from app.utils.table_ocr import (
        pairs_to_table_rows,
        rebuild_two_column_rows,
        rows_to_fact_text,
        rows_to_markdown,
    )

    # Drop tiny junk tokens (logo OCR noise)
    cleaned = [w for w in words if w.text not in {"<>", "|", "—"} and w.conf >= 40]

    pairs = rebuild_two_column_rows(
        cleaned,
        column_split_x=column_split_x,
        min_top=min_top,
        gap_px=22,
        max_pair_distance_px=80,
        header_tokens=frozenset({"particulars", "iculars", "frequency", "sr", "no", "s.no"}),
    )
    rows = pairs_to_table_rows(pairs)

    title = (
        "KSA Compliance Calendar (Kingdom of Saudi Arabia) - "
        "structured OCR (Particulars linked to Frequency)"
    )
    facts = rows_to_fact_text(rows, jurisdiction="KSA", title=title)
    md = rows_to_markdown(rows, title=title)
    return rows, facts, md


def main() -> int:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Structured OCR for KSA Compliance Calendar")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="Path to KSA.pdf")
    parser.add_argument("--column-split-x", type=int, default=650)
    parser.add_argument("--min-top", type=int, default=360)
    parser.add_argument("--zoom", type=float, default=2.0)
    args = parser.parse_args()

    pdf_path: Path = args.pdf
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    print(f"PDF: {pdf_path}")
    img = render_pdf_page(pdf_path, zoom=args.zoom)
    print(f"Rendered page image: {img.size[0]}x{img.size[1]}")

    words = ocr_words(img)
    print(f"OCR words with boxes: {len(words)}")

    rows, facts, md = structure_ksa_calendar(
        words,
        column_split_x=args.column_split_x,
        min_top=args.min_top,
    )
    print(f"Structured rows: {len(rows)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    render_path = OUT_DIR / "ksa_page_render.png"
    facts_path = OUT_DIR / "ksa_ocr_structured_facts.txt"
    md_path = OUT_DIR / "ksa_ocr_structured_table.md"
    dump_path = OUT_DIR / "ksa_ocr_sample.txt"  # keep old dump for comparison

    img.save(render_path)
    facts_path.write_text(facts, encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")

    print()
    print("=== Structured facts (RAG-safe) ===")
    print(facts)
    print(f"Saved facts:     {facts_path}")
    print(f"Saved markdown:  {md_path}")
    print(f"Saved render:    {render_path}")
    print(f"(Old dump still: {dump_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
