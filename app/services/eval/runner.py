"""
Eval runner: load JSONL cases → QueryEngine retrieve (+ optional ask) → metrics → reports.

This reuses production QueryEngine.inspect_retrieve / ask — no second pipeline.
LLM-as-judge (faithfulness / answer_relevance) lands in a later step.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.eval.cases import load_cases
from app.services.eval.metrics import (
    answer_forbid_ok,
    answer_must_include_rate,
    context_recall_proxy,
    hit_at_k,
    mean_skip_none,
)
from app.services.retrieval.engine import QueryEngine
from app.utils.helpers import get_logger

logger = get_logger(__name__)


def _chunk_brief(chunk: Any) -> dict[str, Any]:
    return {
        "chunk_id": getattr(chunk, "chunk_id", ""),
        "score": getattr(chunk, "score", None),
        "source_url": getattr(chunk, "source_url", ""),
        "doc_title": getattr(chunk, "doc_title", ""),
        "heading_path": getattr(chunk, "heading_path", ""),
        "chunk_text_excerpt": (getattr(chunk, "chunk_text", "") or "")[:400],
    }


def evaluate_case(
    engine: QueryEngine,
    case: dict[str, Any],
    *,
    generate: bool = True,
) -> dict[str, Any]:
    query = str(case["query"])
    source = str(case.get("expected_source") or "all")
    urls = case.get("reference_source_urls") or []
    must = case.get("reference_must_include") or []
    forbid = case.get("answer_must_not_include") or []

    chunks = engine.inspect_retrieve(query, source=source)
    hit = hit_at_k(
        chunks,
        reference_source_urls=urls,
        reference_must_include=must,
    )
    recall = context_recall_proxy(chunks, reference_must_include=must)

    answer = ""
    answer_rate: float | None = None
    forbid_ok: float | None = None
    sources: list[dict[str, Any]] = []
    if generate:
        response = engine.ask(query, source=source)
        answer = response.answer or ""
        sources = list(response.sources or [])
        answer_rate = answer_must_include_rate(answer, reference_must_include=must)
        forbid_ok = answer_forbid_ok(answer, answer_must_not_include=forbid)

    return {
        "id": case["id"],
        "query": query,
        "tags": list(case.get("tags") or []),
        "expected_source": source,
        "ground_truth": case.get("ground_truth"),
        "metrics": {
            "hit_at_k": hit,
            "context_recall_proxy": recall,
            "answer_must_include_rate": answer_rate,
            "answer_forbid_ok": forbid_ok,
            # Reserved for Nova judge (next step)
            "faithfulness": None,
            "answer_relevance": None,
        },
        "retrieved": [_chunk_brief(c) for c in chunks],
        "retrieved_urls": [getattr(c, "source_url", "") for c in chunks],
        "answer_excerpt": (answer or "")[:800],
        "answer_sources": sources,
        "generated": generate,
    }


def _by_tag_means(case_rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in case_rows:
        for tag in row.get("tags") or []:
            buckets[str(tag)].append(row.get("metrics") or {})
    out: dict[str, dict[str, float | None]] = {}
    for tag, metrics_list in sorted(buckets.items()):
        out[tag] = {
            "hit_at_k": mean_skip_none([m.get("hit_at_k") for m in metrics_list]),
            "context_recall_proxy": mean_skip_none(
                [m.get("context_recall_proxy") for m in metrics_list]
            ),
            "answer_must_include_rate": mean_skip_none(
                [m.get("answer_must_include_rate") for m in metrics_list]
            ),
            "answer_forbid_ok": mean_skip_none(
                [m.get("answer_forbid_ok") for m in metrics_list]
            ),
            "n": float(len(metrics_list)),
        }
    return out


def build_summary(
    case_rows: list[dict[str, Any]],
    *,
    cases_path: Path,
    run_id: str,
    generate: bool,
    retrieval_cfg: dict[str, Any],
) -> dict[str, Any]:
    metrics_list = [r.get("metrics") or {} for r in case_rows]
    return {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(cases_path),
        "n_cases": len(case_rows),
        "generate": generate,
        "retrieval": retrieval_cfg,
        "means": {
            "hit_at_k": mean_skip_none([m.get("hit_at_k") for m in metrics_list]),
            "context_recall_proxy": mean_skip_none(
                [m.get("context_recall_proxy") for m in metrics_list]
            ),
            "answer_must_include_rate": mean_skip_none(
                [m.get("answer_must_include_rate") for m in metrics_list]
            ),
            "answer_forbid_ok": mean_skip_none(
                [m.get("answer_forbid_ok") for m in metrics_list]
            ),
            "faithfulness": None,
            "answer_relevance": None,
        },
        "by_tag": _by_tag_means(case_rows),
        "case_ids": [r["id"] for r in case_rows],
    }


def write_report(
    out_dir: Path,
    summary: dict[str, Any],
    case_rows: list[dict[str, Any]],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"
    cases_path = out_dir / "cases.jsonl"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    with cases_path.open("w", encoding="utf-8") as f:
        for row in case_rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return out_dir


def freeze_baseline(summary: dict[str, Any], baseline_path: Path) -> Path:
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **summary,
        "baseline_name": baseline_path.stem,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
    }
    baseline_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return baseline_path


def run_eval(
    engine: QueryEngine,
    *,
    cases_path: Path,
    out_dir: Path,
    max_cases: int | None = None,
    tags: list[str] | None = None,
    generate: bool = True,
    freeze_baseline_path: Path | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path, max_cases=max_cases, tags=tags)
    if not cases:
        raise RuntimeError(f"No eval cases loaded from {cases_path}")

    # Prefer deterministic answers during eval runs.
    eval_temp = engine.settings.setting("eval", "temperature", default=0)
    if engine.settings._generator is not None:
        engine.settings._generator.temperature = float(eval_temp)
    else:
        llm_cfg = engine.settings.config.setdefault("llm", {})
        llm_cfg["temperature"] = float(eval_temp)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    case_rows: list[dict[str, Any]] = []
    for i, case in enumerate(cases, start=1):
        logger.info("Eval case %s/%s: %s", i, len(cases), case.get("id"))
        case_rows.append(evaluate_case(engine, case, generate=generate))

    retrieval_cfg = {
        "top_k_initial": engine.settings.setting("retrieval", "top_k_initial", default=20),
        "max_chunks_returned": engine.settings.setting(
            "retrieval", "max_chunks_returned", default=5
        ),
        "max_chunks_per_source_url": engine.settings.setting(
            "retrieval", "max_chunks_per_source_url", default=2
        ),
    }
    summary = build_summary(
        case_rows,
        cases_path=cases_path,
        run_id=run_id,
        generate=generate,
        retrieval_cfg=retrieval_cfg,
    )
    report_dir = out_dir / run_id
    write_report(report_dir, summary, case_rows)
    result: dict[str, Any] = {
        "report_dir": str(report_dir),
        "summary": summary,
    }
    if freeze_baseline_path is not None:
        path = freeze_baseline(summary, freeze_baseline_path)
        result["baseline_path"] = str(path)
    return result
