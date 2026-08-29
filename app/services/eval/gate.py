"""
Compare an eval summary against a frozen baseline (smoke / pre-deploy gate).

Fails if a watched metric drops by more than ``max_drop`` (default 0.05).
Metrics that are null on either side are skipped (e.g. faithfulness before judge).
"""

from __future__ import annotations

from typing import Any


DEFAULT_WATCH = (
    "hit_at_k",
    "context_recall_proxy",
    "faithfulness",
    "answer_relevance",
)


def compare_to_baseline(
    current_means: dict[str, Any],
    baseline_means: dict[str, Any],
    *,
    max_drop: float = 0.05,
    watch: tuple[str, ...] = DEFAULT_WATCH,
) -> dict[str, Any]:
    """
    Return a gate result dict:

    - ok: True if no watched metric drops more than max_drop
    - checks: per-metric detail
    - failures: list of failing metric names
    """
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    for key in watch:
        base = baseline_means.get(key)
        cur = current_means.get(key)
        if base is None or cur is None:
            checks.append(
                {
                    "metric": key,
                    "baseline": base,
                    "current": cur,
                    "delta": None,
                    "status": "skipped",
                    "reason": "null on baseline and/or current",
                }
            )
            continue

        base_f = float(base)
        cur_f = float(cur)
        delta = cur_f - base_f
        dropped = delta < -float(max_drop)
        status = "fail" if dropped else "pass"
        if dropped:
            failures.append(key)
        checks.append(
            {
                "metric": key,
                "baseline": base_f,
                "current": cur_f,
                "delta": delta,
                "max_drop": float(max_drop),
                "status": status,
            }
        )

    return {
        "ok": len(failures) == 0,
        "max_drop": float(max_drop),
        "failures": failures,
        "checks": checks,
    }
