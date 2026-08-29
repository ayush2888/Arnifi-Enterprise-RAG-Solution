"""
Phase 3 — retrieval hyperparameter sweep against the eval set.

Mutates settings.config["retrieval"] in-process (QueryEngine reads knobs per call),
runs retrieve-only eval for each grid point, compares means to baseline_v1.

Usage (from Arnifi-Enterprise-RAG-Solution/):

  python scripts/eval_retrieval_sweep.py --tags smoke
  python scripts/eval_retrieval_sweep.py --tags smoke --quick
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.config.settings import Settings
from app.services.eval.runner import (
    build_summary,
    evaluate_case,
    load_cases,
    write_report,
)
from app.services.retrieval.engine import QueryEngine
from app.utils.helpers import get_logger, setup_logging

logger = get_logger(__name__)

# Plan Phase 3 grid
DEFAULT_GRID = {
    "top_k_initial": [10, 20, 40],
    "max_chunks_returned": [3, 5, 8],
    "max_chunks_per_source_url": [1, 2, 3],
}

# Around current baseline — fewer Bedrock/Pinecone calls
QUICK_GRID = {
    "top_k_initial": [10, 20, 40],
    "max_chunks_returned": [3, 5, 8],
    "max_chunks_per_source_url": [2],
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sweep retrieval knobs vs eval baseline")
    p.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    p.add_argument(
        "--cases",
        default=str(ROOT / "data" / "eval" / "cases.jsonl"),
    )
    p.add_argument(
        "--baseline",
        default=str(ROOT / "data" / "eval" / "baselines" / "baseline_v1.json"),
    )
    p.add_argument(
        "--out",
        default=str(ROOT / "data" / "eval" / "reports"),
    )
    p.add_argument("--tags", default="smoke", help="Comma-separated tags (default: smoke)")
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument(
        "--quick",
        action="store_true",
        help="Smaller grid (fix max_chunks_per_source_url=2)",
    )
    return p.parse_args()


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def set_retrieval(settings: Settings, overrides: dict[str, int]) -> dict[str, int]:
    """Patch live retrieval knobs; return previous values for restore."""
    retrieval = settings.config.setdefault("retrieval", {})
    previous = {
        "top_k_initial": int(retrieval.get("top_k_initial", 20)),
        "max_chunks_returned": int(retrieval.get("max_chunks_returned", 5)),
        "max_chunks_per_source_url": int(retrieval.get("max_chunks_per_source_url", 2)),
    }
    retrieval.update(overrides)
    return previous


def cost_score(params: dict[str, int]) -> int:
    """Lower is cheaper/faster: prefer smaller candidate pools and context packs."""
    return (
        int(params["top_k_initial"])
        + 3 * int(params["max_chunks_returned"])
        + int(params["max_chunks_per_source_url"])
    )


def meets_floor(means: dict, baseline_means: dict, *, drop_tol: float = 0.0) -> bool:
    """True if hit@k and recall do not fall below baseline (nulls ignored)."""
    for key in ("hit_at_k", "context_recall_proxy"):
        base = baseline_means.get(key)
        got = means.get(key)
        if base is None or got is None:
            continue
        if float(got) + drop_tol < float(base):
            return False
    return True


def strictly_better(means: dict, baseline_means: dict) -> bool:
    """At least one metric up, none down (among defined metrics)."""
    improved = False
    for key in ("hit_at_k", "context_recall_proxy"):
        base = baseline_means.get(key)
        got = means.get(key)
        if base is None or got is None:
            continue
        if float(got) < float(base):
            return False
        if float(got) > float(base):
            improved = True
    return improved


def pick_winner(
    rows: list[dict],
    baseline_means: dict,
    baseline_params: dict[str, int],
) -> dict:
    """
    Promote only if we beat the floor. Among safe rows, prefer strict gains,
    then lower cost, then current baseline params.
    """
    safe = [r for r in rows if r.get("meets_floor")]
    if not safe:
        return {
            "decision": "keep_baseline",
            "reason": "No grid point met the baseline floor (hit@k / recall).",
            "params": baseline_params,
        }

    beaters = [r for r in safe if r.get("strictly_better")]
    pool = beaters if beaters else safe

    def sort_key(r: dict) -> tuple:
        m = r["means"]
        hit = m.get("hit_at_k")
        rec = m.get("context_recall_proxy")
        return (
            0 if r.get("strictly_better") else 1,
            -(hit if hit is not None else -1.0),
            -(rec if rec is not None else -1.0),
            cost_score(r["params"]),
            0 if r["params"] == baseline_params else 1,
        )

    best = sorted(pool, key=sort_key)[0]
    if beaters:
        return {
            "decision": "promote",
            "reason": "Beats baseline_v1 on retrieval means without dropping the floor.",
            "params": best["params"],
            "means": best["means"],
        }
    if best["params"] == baseline_params:
        return {
            "decision": "keep_baseline",
            "reason": (
                "Smoke retrieval already at ceiling vs baseline; "
                "no strictly better grid point. Keep current settings."
            ),
            "params": baseline_params,
            "means": best["means"],
        }
    return {
        "decision": "optional_cheaper",
        "reason": (
            "Matches baseline floor; a cheaper grid point ties. "
            "Do not auto-promote — review latency vs robustness."
        ),
        "params": best["params"],
        "means": best["means"],
        "baseline_params": baseline_params,
    }


def run_one(
    engine: QueryEngine,
    cases: list[dict],
    params: dict[str, int],
    *,
    cases_path: Path,
    run_suffix: str,
) -> dict:
    previous = set_retrieval(engine.settings, params)
    try:
        case_rows = [
            evaluate_case(engine, case, generate=False) for case in cases
        ]
        summary = build_summary(
            case_rows,
            cases_path=cases_path,
            run_id=run_suffix,
            generate=False,
            retrieval_cfg=dict(params),
        )
        return {
            "params": dict(params),
            "means": summary["means"],
            "summary": summary,
            "case_rows": case_rows,
        }
    finally:
        set_retrieval(engine.settings, previous)


def main() -> None:
    load_dotenv(ROOT / ".env")
    setup_logging()
    args = _parse_args()

    cases_path = _resolve(args.cases)
    baseline_path = _resolve(args.baseline)
    out_root = _resolve(args.out)
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()] or None

    if not baseline_path.is_file():
        raise SystemExit(f"Baseline not found: {baseline_path}")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_means = baseline.get("means") or {}
    baseline_params = {
        "top_k_initial": int((baseline.get("retrieval") or {}).get("top_k_initial", 20)),
        "max_chunks_returned": int(
            (baseline.get("retrieval") or {}).get("max_chunks_returned", 5)
        ),
        "max_chunks_per_source_url": int(
            (baseline.get("retrieval") or {}).get("max_chunks_per_source_url", 2)
        ),
    }

    grid = QUICK_GRID if args.quick else DEFAULT_GRID
    combos = [
        {
            "top_k_initial": a,
            "max_chunks_returned": b,
            "max_chunks_per_source_url": c,
        }
        for a, b, c in itertools.product(
            grid["top_k_initial"],
            grid["max_chunks_returned"],
            grid["max_chunks_per_source_url"],
        )
    ]

    settings = Settings(args.config)
    try:
        cases = load_cases(cases_path, max_cases=args.max_cases, tags=tags)
        if not cases:
            raise SystemExit("No cases loaded — check --cases / --tags")
        engine = QueryEngine(settings)
        sweep_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sweep_dir = out_root / f"sweep_{sweep_id}"
        sweep_dir.mkdir(parents=True, exist_ok=True)

        rows: list[dict] = []
        for i, params in enumerate(combos, start=1):
            logger.info(
                "Sweep %s/%s params=%s",
                i,
                len(combos),
                params,
            )
            result = run_one(
                engine,
                cases,
                params,
                cases_path=cases_path,
                run_suffix=f"{sweep_id}_{i:02d}",
            )
            means = result["means"]
            row = {
                "params": result["params"],
                "means": {
                    "hit_at_k": means.get("hit_at_k"),
                    "context_recall_proxy": means.get("context_recall_proxy"),
                },
                "cost_score": cost_score(result["params"]),
                "meets_floor": meets_floor(means, baseline_means),
                "strictly_better": strictly_better(means, baseline_means),
                "is_baseline_params": result["params"] == baseline_params,
            }
            rows.append(row)
            # Persist each combo's detailed report for debugging
            write_report(
                sweep_dir / f"combo_{i:02d}",
                result["summary"],
                result["case_rows"],
            )

        decision = pick_winner(rows, baseline_means, baseline_params)
        payload = {
            "sweep_id": sweep_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "baseline_path": str(baseline_path),
            "baseline_means": baseline_means,
            "baseline_params": baseline_params,
            "n_cases": len(cases),
            "tags": tags,
            "grid": grid,
            "n_combos": len(combos),
            "rows": rows,
            "decision": decision,
            "note": (
                "Faithfulness/answer_relevance not used in this retrieve-only sweep. "
                "Promote settings.yaml only on decision=promote."
            ),
        }
        summary_path = sweep_dir / "sweep_summary.json"
        summary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # Human-readable decision note for the eval folder
        notes_path = ROOT / "data" / "eval" / "SWEEP_NOTES.md"
        notes = (
            f"# Retrieval sweep notes\n\n"
            f"- Sweep id: `{sweep_id}`\n"
            f"- Report: `{summary_path}`\n"
            f"- Baseline: `{baseline_path.name}` "
            f"(hit@k={baseline_means.get('hit_at_k')}, "
            f"recall={baseline_means.get('context_recall_proxy')})\n"
            f"- Decision: **{decision.get('decision')}** — {decision.get('reason')}\n"
            f"- Chosen params: `{json.dumps(decision.get('params'))}`\n"
            f"- Combos tried: {len(combos)} ({'quick' if args.quick else 'full'} grid)\n"
            f"- Cases: {len(cases)} tags={tags}\n"
        )
        notes_path.write_text(notes, encoding="utf-8")

        print(
            json.dumps(
                {
                    "sweep_dir": str(sweep_dir),
                    "summary_path": str(summary_path),
                    "notes_path": str(notes_path),
                    "decision": decision,
                    "best_safe_rows": [
                        {
                            "params": r["params"],
                            "means": r["means"],
                            "cost_score": r["cost_score"],
                            "strictly_better": r["strictly_better"],
                        }
                        for r in rows
                        if r["meets_floor"]
                    ][:5],
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
    finally:
        settings.close()


if __name__ == "__main__":
    main()
