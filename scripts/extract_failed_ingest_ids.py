"""Extract failed Drive ingest file IDs from a terminal log JSON result."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> None:
    log_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    text = log_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'\{\s*"extracted_dir"', text)
    if not match:
        raise SystemExit("No ingest result JSON found in log")
    chunk = text[match.start() :]
    footer = chunk.find("\n---\n")
    if footer != -1:
        chunk = chunk[:footer]
    obj = json.loads(chunk)
    errs = obj.get("errors") or []
    ids = [Path(str(e.get("file", ""))).stem for e in errs if e.get("file")]
    out_path.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")
    print(
        json.dumps(
            {
                "files_processed": obj.get("files_processed"),
                "files_failed": obj.get("files_failed"),
                "chunks_upserted": obj.get("chunks_upserted"),
                "failed_ids_written": len(ids),
                "out": str(out_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
