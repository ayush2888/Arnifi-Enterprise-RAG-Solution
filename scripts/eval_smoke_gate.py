"""
Pre-deploy smoke gate: retrieve-only smoke eval vs baseline_v1.

Exit code 0 = pass, 1 = regression beyond max_drop, 2 = setup error.

Usage (from Arnifi-Enterprise-RAG-Solution/):

  python scripts/eval_smoke_gate.py
  python scripts/eval_smoke_gate.py --max-drop 0.05 --tags smoke
  python scripts/eval_smoke_gate.py --summary path/to/summary.json   # offline compare only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.services.eval.gate import compare_to_baseline
from app.utils.helpers import setup_logging


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else ROOT / path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke eval gate vs baseline_v1")
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
    p.add_argument("--tags", default="smoke")
    p.add_argument("--max-drop", type=float, default=0.05)
    p.add_argument(
        "--summary",
        default=None,
        help="Skip live eval; compare this summary.json to baseline (offline)",
    )
    return p.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    setup_logging()
    args = _parse_args()

    baseline_path = _resolve(args.baseline)
    if not baseline_path.is_file():
        print(f"ERROR: baseline not found: {baseline_path}", file=sys.stderr)
        return 2

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_means = baseline.get("means") or {}

    if args.summary:
        summary_path = _resolve(args.summary)
        if not summary_path.is_file():
            print(f"ERROR: summary not found: {summary_path}", file=sys.stderr)
            return 2
        current = json.loads(summary_path.read_text(encoding="utf-8"))
        report_dir = str(summary_path.parent)
    else:
        from app.config.settings import Settings
        from app.services.eval.runner import run_eval
        from app.services.retrieval.engine import QueryEngine

        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        settings = Settings(args.config)
        try:
            result = run_eval(
                QueryEngine(settings),
                cases_path=_resolve(args.cases),
                out_dir=_resolve(args.out),
                tags=tags,
                generate=False,
            )
        finally:
            settings.close()
        current = result["summary"]
        report_dir = result["report_dir"]

    gate = compare_to_baseline(
        current.get("means") or {},
        baseline_means,
        max_drop=args.max_drop,
    )
    payload = {
        "report_dir": report_dir,
        "baseline_path": str(baseline_path),
        "current_means": current.get("means"),
        "baseline_means": baseline_means,
        "gate": gate,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    if not gate["ok"]:
        print(
            f"SMOKE GATE FAILED: {', '.join(gate['failures'])} "
            f"dropped more than {args.max_drop}",
            file=sys.stderr,
        )
        return 1
    print("SMOKE GATE PASSED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
