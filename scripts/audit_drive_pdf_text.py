"""
Read-only audit: find image-only / no-text-layer PDFs in the RAG Drive tree.

Uses Google Drive list (readonly) + local synced raw files when present.
Optionally downloads missing PDFs to a local audit cache (does not change Drive).

Usage (from Arnifi-Enterprise-RAG-Solution/):

  python scripts/audit_drive_pdf_text.py
  python scripts/audit_drive_pdf_text.py --min-chars 20 --min-avg-chars-per-page 25
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.config.env import load_env
from app.services.drive.client import DriveClient
from app.utils.helpers import setup_logging, get_logger

logger = get_logger(__name__)

PDF_MIME = "application/pdf"


@dataclass
class PdfAuditRow:
    folder_path: str
    file_name: str
    full_path: str
    file_id: str
    total_pages: int | None
    total_extracted_chars: int | None
    avg_chars_per_page: float | None
    status: str
    local_path: str
    error: str


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def find_local_raw(raw_dir: Path, file_id: str) -> Path | None:
    if not raw_dir.is_dir():
        return None
    matches = sorted(raw_dir.glob(f"{file_id}_*.pdf"))
    if matches:
        return matches[0]
    direct = raw_dir / f"{file_id}.pdf"
    return direct if direct.is_file() else None


def analyze_pdf(
    path: Path,
    *,
    min_chars: int,
    min_avg_chars_per_page: float,
) -> tuple[int, int, float, str]:
    """Return pages, total_chars, avg_chars_per_page, status."""
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        pages = list(pdf.pages)
        page_count = len(pages)
        total_text = "".join((p.extract_text() or "") for p in pages)
    total_chars = len(total_text.strip())
    avg = (total_chars / page_count) if page_count else 0.0

    if page_count == 0:
        status = "ERROR - NO PAGES"
    elif total_chars < min_chars or avg < min_avg_chars_per_page:
        status = "NO TEXT LAYER - NEEDS OCR"
    else:
        status = "OK"
    return page_count, total_chars, round(avg, 2), status


def main() -> int:
    load_dotenv(ROOT / ".env")
    setup_logging()

    parser = argparse.ArgumentParser(description="Audit Drive PDFs for missing text layers")
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "eval" / "reports" / "drive_pdf_text_audit.csv"),
        help="CSV output path",
    )
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--min-avg-chars-per-page", type=float, default=25.0)
    parser.add_argument(
        "--download-missing",
        action="store_true",
        default=True,
        help="Download PDFs missing from local raw/ into a local audit cache (default: on)",
    )
    parser.add_argument(
        "--no-download-missing",
        action="store_true",
        help="Do not download; mark missing local files as ERROR - NOT SYNCED LOCALLY",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on number of PDFs (debug)",
    )
    args = parser.parse_args()
    download_missing = not args.no_download_missing

    env = load_env()
    sa = env.google_service_account_file
    root_id = env.drive_root_folder_id
    if not sa or not root_id:
        print("ERROR: Set GOOGLE_SERVICE_ACCOUNT_FILE and DRIVE_ROOT_FOLDER_ID in .env", file=sys.stderr)
        return 2

    artifacts = ROOT / "data" / "artifacts"
    raw_dir = artifacts / "drive" / "raw"
    cache_dir = artifacts / "drive" / "audit_pdf_cache"
    if download_missing:
        cache_dir.mkdir(parents=True, exist_ok=True)

    client = DriveClient(_resolve_path(sa), root_id)
    root_name = client.get_folder_name()
    logger.info("Listing Drive tree under %s (%s)", root_name, root_id)
    tree = client.list_tree()
    pdfs = [
        item
        for item in tree
        if (not item.is_folder)
        and (
            (item.mime_type or "").lower() == PDF_MIME
            or (item.name or "").lower().endswith(".pdf")
        )
    ]
    pdfs.sort(key=lambda i: (i.folder_path or "", i.name or ""))
    if args.limit is not None:
        pdfs = pdfs[: max(0, args.limit)]

    logger.info("Found %s PDF files to audit", len(pdfs))
    rows: list[PdfAuditRow] = []

    for i, item in enumerate(pdfs, start=1):
        folder = item.folder_path or ""
        # Prefer Drive folder path; include root name for clarity
        folder_display = f"{root_name}/{folder}" if folder else root_name
        local = find_local_raw(raw_dir, item.file_id)
        source = "raw"
        error = ""
        pages: int | None = None
        chars: int | None = None
        avg: float | None = None
        status = "OK"
        local_path = ""

        try:
            if local is None and download_missing:
                cache_path = cache_dir / f"{item.file_id}_{_safe(item.name)}"
                if not cache_path.is_file():
                    logger.info("[%s/%s] downloading %s", i, len(pdfs), item.full_path)
                    client.download_file(item.file_id, cache_path)
                local = cache_path
                source = "audit_cache"
            if local is None:
                status = "ERROR - NOT SYNCED LOCALLY"
                error = "No local raw copy; re-run without --no-download-missing or drive-sync"
            else:
                local_path = str(local)
                if i % 25 == 1 or i == len(pdfs):
                    logger.info("[%s/%s] analyzing %s", i, len(pdfs), item.full_path)
                pages, chars, avg, status = analyze_pdf(
                    local,
                    min_chars=args.min_chars,
                    min_avg_chars_per_page=args.min_avg_chars_per_page,
                )
        except Exception as exc:  # noqa: BLE001 - collect per-file errors for the report
            status = "ERROR - FAILED TO OPEN"
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("Failed %s: %s", item.full_path, error)

        rows.append(
            PdfAuditRow(
                folder_path=folder_display,
                file_name=item.name,
                full_path=f"{folder_display}/{item.name}" if folder else f"{root_name}/{item.name}",
                file_id=item.file_id,
                total_pages=pages,
                total_extracted_chars=chars,
                avg_chars_per_page=avg,
                status=status,
                local_path=local_path or source,
                error=error,
            )
        )

    out_path = _resolve_path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "folder_path",
        "file_name",
        "full_path",
        "file_id",
        "total_pages",
        "total_extracted_chars",
        "avg_chars_per_page",
        "status",
        "local_path",
        "error",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    needs_ocr = sum(1 for r in rows if r.status == "NO TEXT LAYER - NEEDS OCR")
    ok = sum(1 for r in rows if r.status == "OK")
    errors = [r for r in rows if r.status.startswith("ERROR")]
    total = len(rows)

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "drive_root_name": root_name,
        "drive_root_id": root_id,
        "csv_path": str(out_path),
        "thresholds": {
            "min_chars": args.min_chars,
            "min_avg_chars_per_page": args.min_avg_chars_per_page,
        },
        "total_pdfs": total,
        "ok": ok,
        "needs_ocr": needs_ocr,
        "errors": len(errors),
        "headline": (
            f"{needs_ocr} out of {total} total PDFs have no text layer "
            f"and are not embedding-ready."
        ),
        "error_files": [
            {"full_path": r.full_path, "status": r.status, "error": r.error}
            for r in errors
        ],
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print()
    print(summary["headline"])
    print(f"CSV: {out_path}")
    return 0


def _safe(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in (name or "file"))
    return cleaned.strip().replace(" ", "_")[:120] or "file.pdf"


if __name__ == "__main__":
    raise SystemExit(main())
