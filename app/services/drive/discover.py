"""Summarize Google Drive tree: folders, files, extensions, ingest support."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.services.drive.client import DriveClient, DriveItem
from app.services.ingestion.extractors import is_supported_drive_file


def _extension(name: str) -> str:
    lower = (name or "").lower().strip()
    if "." not in lower:
        return "(none)"
    return Path(lower).suffix or "(none)"


def _folder_key(item: DriveItem) -> str:
    """Immediate parent folder path for grouping files."""
    if item.is_folder:
        return item.full_path or item.name
    return item.folder_path if item.folder_path else "(root)"


def build_discover_report(
    client: DriveClient,
    *,
    folder_id: str | None = None,
) -> dict[str, Any]:
    """
    Walk Drive under root (or folder_id) and return a human/machine summary.

    Does not download files — discovery only.
    """
    root_id = folder_id or client.root_folder_id
    root_name = client.get_folder_name(root_id)
    items = client.list_tree(root_id)

    folders = [i for i in items if i.is_folder]
    files = [i for i in items if not i.is_folder]

    by_ext: Counter[str] = Counter()
    by_mime: Counter[str] = Counter()
    supported: list[dict[str, str]] = []
    unsupported: list[dict[str, str]] = []

    # folder_path -> stats
    per_folder: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "files": 0,
            "supported": 0,
            "unsupported": 0,
            "by_ext": Counter(),
        }
    )

    for f in files:
        ext = _extension(f.name)
        by_ext[ext] += 1
        by_mime[f.mime_type or "(unknown)"] += 1
        parent = _folder_key(f)
        per_folder[parent]["files"] += 1
        per_folder[parent]["by_ext"][ext] += 1

        row = {
            "path": f.full_path,
            "name": f.name,
            "mime_type": f.mime_type,
            "ext": ext,
            "file_id": f.file_id,
        }
        if is_supported_drive_file(f.mime_type, f.name):
            supported.append(row)
            per_folder[parent]["supported"] += 1
        else:
            unsupported.append(row)
            per_folder[parent]["unsupported"] += 1

    # Also list empty folders (folders with zero direct files in tree grouping)
    for folder in folders:
        key = folder.full_path
        if key not in per_folder:
            per_folder[key]  # ensure present with zeros

    folder_rows = []
    for path in sorted(per_folder.keys(), key=lambda p: p.lower()):
        stats = per_folder[path]
        folder_rows.append(
            {
                "folder": path,
                "files": stats["files"],
                "supported_pdf_csv": stats["supported"],
                "unsupported": stats["unsupported"],
                "by_ext": dict(sorted(stats["by_ext"].items(), key=lambda x: (-x[1], x[0]))),
            }
        )

    return {
        "root_folder_id": root_id,
        "root_name": root_name,
        "summary": {
            "total_items": len(items),
            "folders": len(folders),
            "files": len(files),
            "supported_for_ingest": len(supported),
            "unsupported_skipped_by_ingest": len(unsupported),
        },
        "by_extension": dict(sorted(by_ext.items(), key=lambda x: (-x[1], x[0]))),
        "by_mime_type": dict(sorted(by_mime.items(), key=lambda x: (-x[1], x[0]))),
        "folders": folder_rows,
        "supported_files": supported,
        "unsupported_files": unsupported,
    }


def _pad(text: str, width: int, align: str = "left") -> str:
    s = str(text)
    if len(s) > width:
        return s[: max(width - 1, 1)] + "…"
    if align == "right":
        return s.rjust(width)
    return s.ljust(width)


def format_discover_text(report: dict[str, Any]) -> str:
    """Readable terminal summary (tables + sections, no HTML)."""
    summary = report["summary"]
    root = str(report.get("root_name") or "")
    root_id = str(report.get("root_folder_id") or "")

    lines: list[str] = []
    width = 88
    bar = "=" * width
    thin = "-" * width

    lines.append(bar)
    lines.append("  ARNIFI DRIVE DISCOVERY REPORT")
    lines.append(bar)
    lines.append(f"  Root folder : {root}")
    lines.append(f"  Folder ID   : {root_id}")
    lines.append(thin)
    lines.append("  SUMMARY")
    lines.append(thin)
    lines.append(f"  Folders discovered              {_pad(summary['folders'], 8, 'right')}")
    lines.append(f"  Files discovered                {_pad(summary['files'], 8, 'right')}")
    lines.append(f"  Total items (folders + files)   {_pad(summary['total_items'], 8, 'right')}")
    lines.append(
        f"  Supported for ingest (PDF/CSV)  {_pad(summary['supported_for_ingest'], 8, 'right')}"
    )
    lines.append(
        f"  Skipped by ingest (other types) {_pad(summary['unsupported_skipped_by_ingest'], 8, 'right')}"
    )
    lines.append("")

    lines.append(thin)
    lines.append("  FILES BY EXTENSION")
    lines.append(thin)
    lines.append(f"  {_pad('Extension', 14)} {_pad('Count', 8, 'right')}")
    lines.append(f"  {_pad('-' * 12, 14)} {_pad('-' * 8, 8, 'right')}")
    for ext, count in report["by_extension"].items():
        lines.append(f"  {_pad(ext, 14)} {_pad(count, 8, 'right')}")
    lines.append("")

    lines.append(thin)
    lines.append("  PER FOLDER BREAKDOWN")
    lines.append(thin)
    # Folder | Files | Supported | Unsupported | Types
    col_folder, col_files, col_sup, col_un, col_types = 42, 6, 10, 12, 14
    lines.append(
        f"  {_pad('Folder', col_folder)} "
        f"{_pad('Files', col_files, 'right')} "
        f"{_pad('PDF/CSV', col_sup, 'right')} "
        f"{_pad('Skipped', col_un, 'right')} "
        f"{_pad('Types', col_types)}"
    )
    lines.append(
        f"  {_pad('-' * (col_folder - 2), col_folder)} "
        f"{_pad('-' * 5, col_files, 'right')} "
        f"{_pad('-' * 7, col_sup, 'right')} "
        f"{_pad('-' * 8, col_un, 'right')} "
        f"{_pad('-' * 12, col_types)}"
    )
    for row in report["folders"]:
        types = ", ".join(f"{e}={n}" for e, n in row["by_ext"].items()) or "—"
        lines.append(
            f"  {_pad(row['folder'], col_folder)} "
            f"{_pad(row['files'], col_files, 'right')} "
            f"{_pad(row['supported_pdf_csv'], col_sup, 'right')} "
            f"{_pad(row['unsupported'], col_un, 'right')} "
            f"{types}"
        )

    lines.append("")
    lines.append(thin)
    lines.append("  Tip: add --json for full file lists; --out path.json to save the report.")
    lines.append(bar)
    return "\n".join(lines)
