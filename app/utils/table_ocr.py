"""
Rebuild simple 2-column tables from Tesseract word boxes.

Plain OCR reads down columns and breaks Particulars ↔ Frequency links.
Here we keep each word's (x, y), split into left/right columns, cluster into
rows, then emit one complete fact per row for RAG / LLM context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: int

    @property
    def cx(self) -> float:
        return self.left + self.width / 2

    @property
    def cy(self) -> float:
        return self.top + self.height / 2

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass(frozen=True)
class TableRow:
    sr: int
    particulars: str
    frequency: str

    def as_fact(self, *, jurisdiction: str = "KSA") -> str:
        """One self-contained sentence for embedding / LLM context."""
        return (
            f"{jurisdiction} Compliance Calendar - "
            f"Sr {self.sr}: {self.particulars}. "
            f"Frequency: {self.frequency}."
        )

    def as_markdown_row(self) -> str:
        return f"| {self.sr} | {self.particulars} | {self.frequency} |"


def words_from_tesseract_data(data: dict) -> list[OcrWord]:
    """Convert pytesseract image_to_data dict into OcrWord list."""
    out: list[OcrWord] = []
    n = len(data.get("text") or [])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (TypeError, ValueError):
            conf = -1
        if conf < 0:
            continue
        out.append(
            OcrWord(
                text=text,
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
                conf=conf,
            )
        )
    return out


def _join_words(words: Sequence[OcrWord]) -> str:
    return " ".join(w.text for w in words).strip()


def cluster_by_vertical_gap(
    words: Sequence[OcrWord],
    *,
    gap_px: int = 50,
) -> list[list[OcrWord]]:
    """
    Group words into row-blocks.

    Analogy: walk down the page; when the next word jumps down more than
    `gap_px`, start a new physical row-block (a multi-line cell stays together
    if its lines are close).
    """
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (w.top, w.left))
    clusters: list[list[OcrWord]] = [[ordered[0]]]
    prev_bottom = ordered[0].bottom
    for w in ordered[1:]:
        if w.top - prev_bottom > gap_px:
            clusters.append([w])
        else:
            clusters[-1].append(w)
        prev_bottom = max(prev_bottom, w.bottom)
    return clusters


def _block_bounds(block: Sequence[OcrWord]) -> tuple[int, int]:
    return min(w.top for w in block), max(w.bottom for w in block)


def _vertical_range_distance(a: Sequence[OcrWord], b: Sequence[OcrWord]) -> int:
    """0 if ranges overlap; otherwise the gap in pixels between them."""
    a_top, a_bot = _block_bounds(a)
    b_top, b_bot = _block_bounds(b)
    if a_bot < b_top:
        return b_top - a_bot
    if b_bot < a_top:
        return a_top - b_bot
    return 0


def _block_text(block: Sequence[OcrWord]) -> str:
    """
    Approximate human reading order inside a cell.
    Bucket words into visual lines via top//20, then left-to-right.
    """
    return _join_words(sorted(block, key=lambda w: (w.top // 20, w.left)))


def _strip_leading_sr(text: str) -> tuple[int | None, str]:
    """Pull a leading serial number out of particulars if OCR glued it on."""
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit():
        return int(parts[0]), parts[1].strip()
    return None, text


def rebuild_two_column_rows(
    words: Sequence[OcrWord],
    *,
    column_split_x: int,
    min_top: int = 0,
    gap_px: int = 22,
    header_tokens: frozenset[str] | None = None,
    max_pair_distance_px: int | None = None,
) -> list[tuple[str, str]]:
    """
    Split page words into left/right columns and pair blocks by vertical overlap.

    Returns list of (left_text, right_text) in top-to-bottom order.
    """
    header_tokens = header_tokens or frozenset()
    max_dist = max_pair_distance_px if max_pair_distance_px is not None else gap_px * 4
    body = [w for w in words if w.top >= min_top]
    left = [w for w in body if w.cx < column_split_x]
    right = [w for w in body if w.cx >= column_split_x]

    left_blocks = cluster_by_vertical_gap(left, gap_px=gap_px)
    right_blocks = cluster_by_vertical_gap(right, gap_px=gap_px)

    used_right: set[int] = set()
    pairs: list[tuple[str, str]] = []

    for lb in left_blocks:
        lt = _block_text(lb)
        low = lt.lower().strip()
        # Skip header-ish left cells ("Particulars", OCR "iculars")
        if low in header_tokens or low.replace(".", "") in header_tokens:
            continue
        if "particular" in low and len(low) < 20:
            continue
        if low.isdigit():
            continue

        best_i = None
        best_dist = None
        for i, rb in enumerate(right_blocks):
            if i in used_right:
                continue
            rt = _block_text(rb)
            rt_low = rt.lower().strip()
            if rt_low in header_tokens or rt_low == "frequency":
                used_right.add(i)
                continue
            dist = _vertical_range_distance(lb, rb)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_i = i

        right_text = ""
        if best_i is not None and best_dist is not None and best_dist <= max_dist:
            used_right.add(best_i)
            right_text = _block_text(right_blocks[best_i])

        if lt or right_text:
            pairs.append((lt, right_text))

    return pairs


def pairs_to_table_rows(pairs: Sequence[tuple[str, str]]) -> list[TableRow]:
    rows: list[TableRow] = []
    next_sr = 1
    for particulars, frequency in pairs:
        sr_from_text, particulars_clean = _strip_leading_sr(particulars.strip())
        sr = sr_from_text if sr_from_text is not None else next_sr
        next_sr = max(next_sr, sr) + 1
        rows.append(
            TableRow(
                sr=sr,
                particulars=particulars_clean.strip(),
                frequency=frequency.strip(),
            )
        )
    return rows


def rows_to_fact_text(
    rows: Sequence[TableRow],
    *,
    jurisdiction: str,
    title: str,
) -> str:
    lines = [title, ""]
    for row in rows:
        lines.append(row.as_fact(jurisdiction=jurisdiction))
    return "\n".join(lines).strip() + "\n"


def rows_to_markdown(
    rows: Sequence[TableRow],
    *,
    title: str,
) -> str:
    lines = [
        title,
        "",
        "| Sr | Particulars | Frequency |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        lines.append(row.as_markdown_row())
    return "\n".join(lines).strip() + "\n"
