from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_PRICING_PATH = _ROOT / "config" / "pricing.yaml"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not _PRICING_PATH.is_file():
        return {}
    return yaml.safe_load(_PRICING_PATH.read_text(encoding="utf-8")) or {}


def cost_usd(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    cfg = _load()
    models = cfg.get("models") or {}
    rates = models.get(model_id) or cfg.get("default") or {}
    inp = float(rates.get("input") or 0)
    out = float(rates.get("output") or 0)
    return (prompt_tokens / 1_000_000) * inp + (completion_tokens / 1_000_000) * out


def org_monthly_budget_usd() -> float:
    return float((_load().get("org_monthly_budget_usd") or 100))
