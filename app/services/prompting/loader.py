"""Load system prompts from config/prompts without embedding AWS details."""

from __future__ import annotations

from pathlib import Path

_DEFAULT_PROMPT = (
    "Answer using only the provided context. Cite sources with [Source N]."
)


def load_prompt(prompt_path: str | Path | None) -> str:
    if prompt_path is None:
        return _DEFAULT_PROMPT
    path = Path(prompt_path)
    if not path.exists():
        return _DEFAULT_PROMPT
    return path.read_text(encoding="utf-8")
