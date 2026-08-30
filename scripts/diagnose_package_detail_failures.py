"""Diagnose the 13 PARTIAL/FAIL country-catalog package_detail cases (read-only).

Does NOT re-run the full eval matrix, re-chunk, or modify Pinecone.
Loads known-bad rows from country_catalog_report.csv, re-queries each,
logs top-3 retrieval metadata, classifies failure buckets, writes a report.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.config.settings import Settings
from app.services.retrieval.engine import QueryEngine
from app.utils.helpers import setup_logging

REPORT_CSV = ROOT / "data" / "eval" / "country_catalog_report.csv"
OUT_JSON = ROOT / "data" / "eval" / "package_detail_failure_diagnostics.json"
OUT_MD = ROOT / "data" / "eval" / "package_detail_failure_diagnostics.md"

BUCKETS = (
    "cross_service_bleed",
    "cross_package_bleed",
    "wrong_section_right_package",
    "right_chunk_incomplete",
    "correct_retrieval_wrong_answer",
    "no_retrieval",
    "ambiguous",
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _tokens(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "or",
        "of",
        "a",
        "an",
        "in",
        "for",
        "to",
        "with",
        "package",
        "setup",
        "company",
        "formation",
        "incorporation",
        "arnifi",
    }
    return {t for t in _norm(text).split() if len(t) > 2 and t not in stop}


def package_match(expected: str, retrieved: str) -> bool:
    """Fuzzy package match: shared distinctive tokens or substring either way."""
    e, r = _norm(expected), _norm(retrieved)
    if not e or not r:
        return False
    if e in r or r in e:
        return True
    et, rt = _tokens(expected), _tokens(retrieved)
    if not et or not rt:
        return False
    overlap = et & rt
    # Require majority of the shorter name's tokens
    need = max(1, (min(len(et), len(rt)) + 1) // 2)
    return len(overlap) >= need


def service_match(expected: str, retrieved: str) -> bool:
    e, r = _norm(expected), _norm(retrieved)
    if not e or not r:
        return False
    if e in r or r in e:
        return True
    # Country-as-service proxies used in this eval (setup packages)
    aliases = {
        "funds": {"funds", "fund"},
        "licence": {"licence", "license", "setup", "business setup"},
    }
    for group in aliases.values():
        if any(a in e for a in group) and any(a in r for a in group):
            return True
    return False


def section_label_from_meta(meta: dict[str, Any], heading_path: str) -> str:
    sec = (meta.get("catalog_section") or "").strip()
    if sec:
        return sec
    path = (heading_path or meta.get("heading_path") or "").strip()
    if path:
        return path.split(">")[0].strip() or path
    return ""


def package_from_meta(meta: dict[str, Any], doc_title: str) -> str:
    return (
        (meta.get("catalog_package") or "").strip()
        or (doc_title or meta.get("doc_title") or "").strip()
    )


def service_from_meta(meta: dict[str, Any]) -> str:
    return (
        (meta.get("catalog_service") or "").strip()
        or (meta.get("doc_category") or "").strip()
        or (meta.get("page_kind") or "").strip()
    )


def load_bad_package_detail_cases(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("Section") or "").strip() != "package_detail":
                continue
            status = (row.get("Pass/Partial/Fail") or "").strip().upper()
            if status not in {"PARTIAL", "FAIL"}:
                continue
            query = (row.get("Query Used") or "").strip()
            if not query:
                continue
            # Pull package name from the eval query template
            m = re.search(r"package detail page for '([^']+)'", query)
            package_name = m.group(1) if m else ""
            rows.append(
                {
                    "country": (row.get("Country") or "").strip(),
                    "query": query,
                    "package_name": package_name,
                    "verdict": status,
                    "notes": (row.get("Notes") or "").strip(),
                }
            )
    return rows


def infer_expected_service(package_name: str, country: str) -> str:
    """Best-effort service label for country-catalog package_detail cases."""
    p = _norm(package_name)
    if "fund" in p or "collective investment" in p or "soparfi" in p and "holding" in p:
        # SOPARFI is holding company (licence); Jersey Expert Fund is Funds
        pass
    if any(k in p for k in ("fund", "collective investment", "expert fund", "closed-ended", "open-ended")):
        return "Funds"
    # Most country Top Packages are setup/licence products
    return f"business-setup ({country})"


def classify_case(
    *,
    expected_service: str,
    expected_package: str,
    top: list[dict[str, Any]],
    answer: str,
    notes: str,
) -> tuple[str, str]:
    """Return (bucket, reason)."""
    if not top:
        return "no_retrieval", "No chunks returned from retrieve pipeline."

    t1 = top[0]
    svc_ok = service_match(expected_service, t1["metadata"].get("service") or "")
    # Also accept country name appearing in retrieved package/service for setup SKUs
    if not svc_ok:
        # Soft: if package matches, treat service as OK for setup products
        svc_ok = package_match(expected_package, t1["metadata"].get("package_name") or "")
    pkg_ok = package_match(expected_package, t1["metadata"].get("package_name") or "")

    # Any of top-3 matching the expected package?
    any_pkg = any(
        package_match(expected_package, r["metadata"].get("package_name") or "")
        for r in top
    )
    any_svc = any(
        service_match(expected_service, r["metadata"].get("service") or "")
        or package_match(expected_package, r["metadata"].get("package_name") or "")
        for r in top
    )

    if not any_svc and not any_pkg:
        return (
            "cross_service_bleed",
            f"Top-1 service/package unrelated to expected "
            f"({t1['metadata'].get('service')!r} / {t1['metadata'].get('package_name')!r}).",
        )

    if not pkg_ok and not any_pkg:
        return (
            "cross_package_bleed",
            f"Expected package not in top-3; top-1={t1['metadata'].get('package_name')!r}.",
        )

    if not pkg_ok and any_pkg:
        return (
            "cross_package_bleed",
            "Correct package appears in top-3 but not at rank 1 "
            f"(top-1={t1['metadata'].get('package_name')!r}).",
        )

    # Package is correct at top-1 (or we soft-matched via package).
    # These eval queries ask for ALL sections ΓÇö "wrong section" = top-1 is a
    # single narrow section while answer still misses other GT sections.
    top1_section = (t1["metadata"].get("section") or "").lower()
    missing_hint = ""
    if "Missing:" in notes:
        missing_hint = notes.split("Missing:", 1)[1].strip()

    # If top-3 all same package and include multiple section types, retrieval OK
    sections_seen = {
        (r["metadata"].get("section") or "").split(">")[0].strip().lower()
        for r in top
        if package_match(expected_package, r["metadata"].get("package_name") or "")
    }
    multi_section = len({s for s in sections_seen if s}) >= 2

    # Coverage failures with correct package in top-1
    if pkg_ok or any_pkg:
        # Generation path: package chunks present but answer still incomplete
        ans_norm = _norm(answer)
        # Heuristic: if answer is very short vs missing list, generation issue
        if multi_section and len(ans_norm) > 80:
            return (
                "correct_retrieval_wrong_answer",
                "Top-3 includes correct package across multiple sections, "
                "but synthesized answer still missed GT phrases "
                f"(missing sample: {missing_hint[:120]}).",
            )
        if top1_section in {
            "introduction",
            "highlights_and_benefits",
            "highlights",
            "process_flow",
            "faqs",
            "faq",
            "terms_and_conditions",
            "terms",
            "pricing",
        } and not multi_section:
            return (
                "wrong_section_right_package",
                f"Right package but top-3 stuck on section={top1_section!r}; "
                "multi-section coverage missing for a package_detail summary query.",
            )
        return (
            "right_chunk_incomplete",
            "Correct package retrieved, but chunk text and/or answer incomplete "
            f"vs GT (missing sample: {missing_hint[:120]}).",
        )

    return "ambiguous", "Could not confidently map top-3 metadata to a bucket."


def diagnose_case(engine: QueryEngine, case: dict[str, str]) -> dict[str, Any]:
    query = case["query"]
    expected_package = case["package_name"]
    expected_service = infer_expected_service(expected_package, case["country"])
    # Expected section: these country-eval cases ask for a multi-section summary
    expected_section = "package_detail_summary(all)"

    chunks = engine.inspect_retrieve(query, source="website")
    top3: list[dict[str, Any]] = []
    for i, ch in enumerate(chunks[:3], start=1):
        meta = dict(ch.metadata or {})
        top3.append(
            {
                "rank": i,
                "score": round(float(ch.score), 4),
                "metadata": {
                    "service": service_from_meta(meta),
                    "package_name": package_from_meta(meta, ch.doc_title),
                    "section": section_label_from_meta(meta, ch.heading_path),
                    "chunk_id": ch.chunk_id,
                    "source_url": ch.source_url,
                    "page_kind": meta.get("page_kind"),
                },
                "text_preview": (ch.chunk_text or "")[:150],
            }
        )

    resp = engine.ask(query, source="website")
    answer = (resp.answer or "").strip()

    bucket, reason = classify_case(
        expected_service=expected_service,
        expected_package=expected_package,
        top=top3,
        answer=answer,
        notes=case["notes"],
    )

    return {
        "country": case["country"],
        "query": query,
        "expected": {
            "service": expected_service,
            "package_name": expected_package,
            "section": expected_section,
            "country": case["country"],
        },
        "top_3_retrieved": top3,
        "answer_preview": answer[:400],
        "original_verdict": case["verdict"],
        "original_notes": case["notes"],
        "failure_bucket": bucket,
        "classification_notes": reason,
    }


def write_report(results: list[dict[str, Any]]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(results),
        "prompt_adaptation": (
            "Country-catalog package_detail queries ask for a multi-section "
            "package summary (intro+highlights+process+FAQs+terms), not one "
            "fixed section. expected.section reflects that. Classification "
            "still uses top-3 metadata hierarchy when present."
        ),
        "cases": results,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    bucket_counts = Counter(r["failure_bucket"] for r in results)
    section_counter: Counter[str] = Counter()
    package_counter: Counter[str] = Counter()
    service_counter: Counter[str] = Counter()
    for r in results:
        package_counter[r["expected"]["package_name"]] += 1
        service_counter[r["expected"]["service"]] += 1
        for hit in r.get("top_3_retrieved") or []:
            sec = (hit.get("metadata") or {}).get("section") or ""
            if sec:
                section_counter[sec.split(">")[0].strip()] += 1

    lines: list[str] = [
        "# Package detail failure diagnostics (13 bad cases)",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Prompt review (adapted)",
        "",
        payload["prompt_adaptation"],
        "",
        "Read-only: no re-chunk / re-embed / index writes.",
        "",
        "## Summary ΓÇö failure buckets",
        "",
    ]
    for b in BUCKETS:
        if bucket_counts.get(b):
            lines.append(f"- **{b}**: {bucket_counts[b]}")
    lines += ["", "## Section types appearing in top-3 of failing cases", ""]
    for sec, n in section_counter.most_common(12):
        lines.append(f"- `{sec}`: {n}")
    lines += ["", "## Expected packages appearing in >1 failing case", ""]
    multi_pkg = [(p, n) for p, n in package_counter.items() if n > 1]
    if multi_pkg:
        for p, n in multi_pkg:
            lines.append(f"- {p}: {n}")
    else:
        lines.append("- None (each failing case targets a distinct package).")
    lines += ["", "## Expected services appearing in >1 failing case", ""]
    multi_svc = [(s, n) for s, n in service_counter.items() if n > 1]
    if multi_svc:
        for s, n in sorted(multi_svc, key=lambda x: -x[1]):
            lines.append(f"- {s}: {n}")
    else:
        lines.append("- None.")

    lines += [
        "",
        "## Case table",
        "",
        "| Query (short) | Expected (Service / Package / Section) | Top-1 Retrieved | Failure Bucket | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        q_short = r["query"]
        if len(q_short) > 70:
            q_short = q_short[:67] + "..."
        exp = r["expected"]
        exp_cell = f"{exp['service']} / {exp['package_name'][:40]} / {exp['section']}"
        if r["top_3_retrieved"]:
            t1 = r["top_3_retrieved"][0]["metadata"]
            top_cell = (
                f"{t1.get('service') or 'ΓÇö'} / "
                f"{(t1.get('package_name') or 'ΓÇö')[:40]} / "
                f"{t1.get('section') or 'ΓÇö'}"
            )
        else:
            top_cell = "(none)"
        note = (r.get("classification_notes") or "")[:120].replace("|", "/")
        lines.append(
            f"| {q_short.replace('|', '/')} | {exp_cell.replace('|', '/')} | "
            f"{top_cell.replace('|', '/')} | `{r['failure_bucket']}` | {note} |"
        )

    lines += ["", f"Full JSON: `{OUT_JSON.relative_to(ROOT).as_posix()}`", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose package_detail PARTIAL/FAIL cases")
    parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    parser.add_argument("--csv", default=str(REPORT_CSV))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    setup_logging()

    cases = load_bad_package_detail_cases(Path(args.csv))
    if args.limit:
        cases = cases[: args.limit]
    print(f"Loaded {len(cases)} PARTIAL/FAIL package_detail cases")
    if len(cases) != 13 and args.limit is None:
        print(f"WARNING: expected 13 cases, found {len(cases)}")

    settings = Settings(args.config)
    results: list[dict[str, Any]] = []
    try:
        engine = QueryEngine(settings)
        for i, case in enumerate(cases, start=1):
            print(f"[{i}/{len(cases)}] {case['country']} / {case['package_name'][:50]}")
            results.append(diagnose_case(engine, case))
    finally:
        settings.close()

    write_report(results)
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")
    counts = Counter(r["failure_bucket"] for r in results)
    print("Bucket counts:", dict(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
