"""Load eval case definitions from JSONL (no QueryEngine / cloud deps)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_cases(
    path: Path,
    *,
    max_cases: int | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON — {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{line_no}: expected a JSON object")
        for key in ("id", "query", "ground_truth", "tags"):
            if key not in obj:
                raise ValueError(f"{path}:{line_no}: missing required field '{key}'")
        case_tags = obj.get("tags") or []
        if tags:
            wanted = set(tags)
            if not wanted.intersection(case_tags):
                continue
        rows.append(obj)
        if max_cases is not None and len(rows) >= max_cases:
            break
    return rows
