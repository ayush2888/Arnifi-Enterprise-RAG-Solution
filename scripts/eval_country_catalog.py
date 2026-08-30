"""
Country catalog agent eval ΓÇö response vs prodapi ground truth.

Ground truth: live GET https://prodapi.arnifi.com/api/country-overview/:slug
Agent: QueryEngine.ask(..., source=\"website\")  (same as CLI / portal)

Usage (from Arnifi-Enterprise-RAG-Solution/):

  .\\venv\\Scripts\\python.exe scripts\\eval_country_catalog.py
  .\\venv\\Scripts\\python.exe scripts\\eval_country_catalog.py --limit-countries 2
  .\\venv\\Scripts\\python.exe scripts\\eval_country_catalog.py --sections selling_points,faq
  .\\venv\\Scripts\\python.exe scripts\\eval_country_catalog.py --refresh-gt

Report: data/eval/country_catalog_report.md (+ .csv)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.config.settings import Settings
from app.services.prodapi.client import ProdapiClient, country_page_url
from app.services.retrieval.engine import QueryEngine
from app.utils.helpers import setup_logging

GT_DIR = ROOT / "data" / "eval" / "country_ground_truth"
REPORT_MD = ROOT / "data" / "eval" / "country_catalog_report.md"
REPORT_CSV = ROOT / "data" / "eval" / "country_catalog_report.csv"

# Consistent query templates (<<country>> filled per case).
SECTION_QUERIES: dict[str, str] = {
    "market_insights": (
        "From the {country} country page on Arnifi, what are the recent market insights?"
    ),
    "selling_points": (
        "From the {country} country page, list all the key selling points exactly."
    ),
    "overview": (
        "From the Arnifi country page, give the country overview for {country}."
    ),
    "process_flow": (
        "From the {country} country page, what does the application process look like? "
        "List every step."
    ),
    "faq": (
        "From the {country} location page FAQ section, list all questions and answers."
    ),
    "cross_compare": (
        "From the Arnifi country pages, compare Saudi Arabia vs UAE vs Singapore "
        "on setup timeline, capital requirement, corporate tax, and VAT/GST."
    ),
    "generic_compare": (
        "Compare {country} vs {peer} using Arnifi's country comparison data "
        "(timeline, tax, capital, VAT if available)."
    ),
    "packages": (
        "From the {country} country page, what top packages or funds does Arnifi list? "
        "Include names and starting prices."
    ),
    "package_detail": (
        "From Arnifi's package detail page for '{title}', summarize the introduction, "
        "highlights/benefits, process flow, FAQs, and terms if available. "
        "Include pricing if shown."
    ),
}


@dataclass
class GroundTruth:
    name: str
    slug: str
    shortcode: str
    source_url: str
    market_insights: list[dict[str, str]]
    selling_points: list[str]
    overview: str
    process_steps: list[tuple[str, str]]
    faqs: list[tuple[str, str]]
    compare: list[dict[str, Any]]
    packages: list[dict[str, Any]]
    funds: list[dict[str, Any]]

    def section_nonempty(self, section: str) -> bool:
        if section == "market_insights":
            return bool(self.market_insights)
        if section == "selling_points":
            return bool(self.selling_points)
        if section == "overview":
            return len(self.overview) >= 40
        if section == "process_flow":
            return bool(self.process_steps)
        if section == "faq":
            return bool(self.faqs)
        if section == "cross_compare":
            names = {c.get("countryName", "").lower() for c in self.compare}
            return {"saudi arabia", "uae", "singapore"} <= names or names >= {
                "uae",
                "singapore",
            }
        if section == "generic_compare":
            return len(self.compare) >= 2
        if section == "packages":
            return bool(self.packages) or bool(self.funds)
        return False


@dataclass
class CaseResult:
    country: str
    section: str
    query: str
    status: str  # PASS | PARTIAL | FAIL | N/A
    notes: str
    coverage: float = 0.0
    present: bool = False
    leak_countries: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", (text or "").lower()).strip()


def payload_to_gt(country_meta: dict[str, Any], payload: dict[str, Any]) -> GroundTruth:
    name = str(country_meta.get("countryName") or payload.get("title") or "")
    slug = str(country_meta.get("slug") or "")
    shortcode = str(country_meta.get("shortcode") or "")
    insights = []
    for row in payload.get("marketInsights") or []:
        if isinstance(row, dict):
            insights.append(
                {
                    "title": _clean(str(row.get("title") or "")),
                    "text": _clean(str(row.get("text") or "")),
                }
            )
    selling = []
    for row in payload.get("keySellingPoints") or []:
        if isinstance(row, dict):
            selling.append(_clean(str(row.get("value") or "")))
        else:
            selling.append(_clean(str(row)))
    process = []
    for row in payload.get("processStep") or []:
        if isinstance(row, dict):
            q = _clean(str(row.get("question") or ""))
            a = _clean(str(row.get("answer") or ""))
            if q and a:
                process.append((q, a))
    faqs = []
    for row in payload.get("FAQs") or []:
        if isinstance(row, dict):
            q = _clean(str(row.get("question") or ""))
            a = _clean(str(row.get("answer") or ""))
            if q and a:
                faqs.append((q, a))
    packages = []
    for row in payload.get("productPages") or []:
        if isinstance(row, dict):
            packages.append(
                {
                    "title": _clean(
                        str(row.get("productName") or row.get("title") or "")
                    ),
                    "price": row.get("startingArnifiPrice")
                    or row.get("startingPrice")
                    or 0,
                    "slug": _clean(str(row.get("slug") or "")),
                    "id": row.get("id"),
                }
            )
    funds = []
    for row in payload.get("funds") or []:
        if isinstance(row, dict):
            funds.append(
                {
                    "title": _clean(str(row.get("title") or "")),
                    "price": row.get("startingPrice") or 0,
                    "slug": _clean(str(row.get("slug") or "")),
                }
            )
    overview = _clean(
        " ".join(
            [
                _clean(str(payload.get("subHeader") or "")),
                _clean(str(payload.get("overviewDetail") or "")),
            ]
        )
    )
    return GroundTruth(
        name=name,
        slug=slug,
        shortcode=shortcode,
        source_url=country_page_url(shortcode, slug=slug),
        market_insights=insights,
        selling_points=[s for s in selling if s],
        overview=overview,
        process_steps=process,
        faqs=faqs,
        compare=list(payload.get("compare") or []),
        packages=packages,
        funds=funds,
    )


def load_or_fetch_ground_truth(
    client: ProdapiClient, *, refresh: bool
) -> list[GroundTruth]:
    GT_DIR.mkdir(parents=True, exist_ok=True)
    countries = client.list_countries()
    out: list[GroundTruth] = []
    for meta in countries:
        slug = str(meta.get("slug") or "")
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", slug) or "country"
        path = GT_DIR / f"{safe}.json"
        if path.exists() and not refresh:
            payload = json.loads(path.read_text(encoding="utf-8"))
            # file stores {"meta":..., "overview":...}
            out.append(payload_to_gt(payload["meta"], payload["overview"]))
            continue
        overview = client.get_country_overview(slug)
        path.write_text(
            json.dumps({"meta": meta, "overview": overview}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        out.append(payload_to_gt(meta, overview))
        time.sleep(0.15)
    return out


def _phrases_for_section(gt: GroundTruth, section: str) -> list[str]:
    if section == "market_insights":
        phrases: list[str] = []
        for row in gt.market_insights:
            if row.get("title"):
                phrases.append(row["title"])
            # first ~12 significant words of body
            words = row.get("text", "").split()
            if len(words) >= 8:
                phrases.append(" ".join(words[:12]))
        return phrases
    if section == "selling_points":
        phrases = []
        for point in gt.selling_points:
            # First line / title-ish head
            head = point.split("\n", 1)[0]
            phrases.append(head[:80])
            # A distinctive number if present
            for m in re.findall(r"\d+(?:\.\d+)?%?", point):
                if len(m) >= 2:
                    phrases.append(m)
                    break
        return phrases
    if section == "overview":
        words = gt.overview.split()
        chunks = []
        if len(words) >= 10:
            chunks.append(" ".join(words[:15]))
        if len(words) >= 40:
            chunks.append(" ".join(words[20:35]))
        return chunks or [gt.overview[:120]]
    if section == "process_flow":
        return [q for q, _ in gt.process_steps]
    if section == "faq":
        return [q for q, _ in gt.faqs]
    if section == "packages":
        items = gt.packages + gt.funds
        phrases = []
        for item in items:
            if item.get("title"):
                phrases.append(item["title"])
            price = item.get("price")
            if price:
                phrases.append(str(price))
        return phrases
    if section in {"cross_compare", "generic_compare"}:
        phrases = []
        for peer in gt.compare:
            for row in peer.get("comparison") or []:
                key = _clean(str(row.get("key") or ""))
                val = _clean(str(row.get("value") or ""))
                if val:
                    phrases.append(val)
                if key and val:
                    phrases.append(f"{key} {val}")
        return phrases
    return []


def _phrases_from_micro_service(payload: dict[str, Any]) -> tuple[str, list[str]]:
    title = _clean(str(payload.get("title") or payload.get("slug") or "package"))
    phrases: list[str] = [title]
    intro = _clean(str(payload.get("description") or payload.get("shortDescription") or ""))
    if intro:
        words = intro.split()
        phrases.append(" ".join(words[:12]) if len(words) >= 8 else intro[:80])
    for key in ("highlightsAndBenifits", "processFlow", "faqs", "termsAndConditions"):
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            head = _clean(
                str(
                    item.get("heading")
                    or item.get("question")
                    or item.get("title")
                    or item.get("termAndCondition")
                    or ""
                )
            )
            if head:
                phrases.append(head[:90])
    price = payload.get("price") or payload.get("startingPrice")
    if price not in (None, "", 0, "0"):
        phrases.append(str(price))
    return title, phrases


def _phrases_from_setup_product(row: dict[str, Any]) -> tuple[str, list[str]]:
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else row
    title = _clean(str(attrs.get("productName") or attrs.get("title") or "package"))
    phrases: list[str] = [title]
    intro = _clean(str(attrs.get("description") or ""))
    if intro:
        words = intro.split()
        phrases.append(" ".join(words[:12]) if len(words) >= 8 else intro[:80])
    for item in attrs.get("faqs") or []:
        if isinstance(item, dict):
            q = _clean(str(item.get("question") or ""))
            if q:
                phrases.append(q[:90])
    price = attrs.get("startingArnifiPrice") or attrs.get("startingOfficialPrice")
    if price not in (None, "", 0, "0"):
        phrases.append(str(price))
    return title, phrases


def resolve_package_detail(
    client: ProdapiClient, gt: GroundTruth
) -> tuple[str, list[str]] | None:
    """Pick one Top Fund (preferred) or setup package and build GT phrases from detail API."""
    if gt.funds:
        fund = gt.funds[0]
        slug = str(fund.get("slug") or "").strip()
        if slug:
            detail = client.get_micro_service_detail("Funds", slug)
            return _phrases_from_micro_service(detail)
    for pkg in gt.packages:
        pid = pkg.get("id")
        if pid is None:
            continue
        row = client.get_product_page_by_id(pid)
        if row:
            return _phrases_from_setup_product(row)
    return None


def _coverage(answer: str, phrases: list[str]) -> tuple[float, list[str], list[str]]:
    if not phrases:
        return 1.0, [], []
    ans = _norm(answer)
    hit: list[str] = []
    miss: list[str] = []
    for p in phrases:
        pn = _norm(p)
        if len(pn) < 4:
            continue
        # Require most tokens present (order-free) for long phrases
        tokens = [t for t in pn.split() if len(t) > 2][:8]
        if not tokens:
            continue
        need = max(1, int(round(len(tokens) * 0.7)))
        found = sum(1 for t in tokens if t in ans)
        if found >= need or pn in ans:
            hit.append(p)
        else:
            miss.append(p)
    total = len(hit) + len(miss)
    if total == 0:
        return 1.0, hit, miss
    return len(hit) / total, hit, miss


def _is_refusal(answer: str) -> bool:
    a = (answer or "").lower().strip()
    if not a:
        return True
    if "could not find relevant" in a:
        return True
    if len(a) < 220 and (
        "i don't have" in a
        or "do not have enough" in a
        or "not available in the provided context" in a
    ):
        return True
    return False


_OTHER_COUNTRIES = [
    "guernsey",
    "saint vincent",
    "uae",
    "saudi arabia",
    "british virgin",
    "cayman",
    "singapore",
    "mauritius",
    "cyprus",
    "hong kong",
    "ireland",
    "luxembourg",
    "malaysia",
    "puerto rico",
    "united kingdom",
]


def _leak_countries(answer: str, current: str, *, allowed_extra: list[str] | None = None) -> list[str]:
    ans = _norm(answer)
    current_n = _norm(current)
    allowed = {_norm(current)}
    for extra in allowed_extra or []:
        allowed.add(_norm(extra))
    leaks = []
    for name in _OTHER_COUNTRIES:
        nn = _norm(name)
        if nn in allowed:
            continue
        if nn in current_n:
            continue
        # Avoid tiny false positives
        if len(nn) < 5:
            continue
        if nn in ans:
            leaks.append(name)
    return leaks


def _peer_for_generic(gt: GroundTruth) -> str | None:
    for peer in gt.compare:
        name = _clean(str(peer.get("countryName") or ""))
        if name and _norm(name) != _norm(gt.name):
            return name
    return None


def score_case(
    *,
    gt: GroundTruth,
    section: str,
    query: str,
    answer: str,
    extra_phrases: list[str] | None = None,
) -> CaseResult:
    if section == "package_detail" and not extra_phrases:
        return CaseResult(
            country=gt.name,
            section=section,
            query=query,
            status="N/A",
            notes="N/A ΓÇö no fund/package detail available from prodapi for this country.",
        )

    if section != "package_detail" and not gt.section_nonempty(section):
        return CaseResult(
            country=gt.name,
            section=section,
            query=query,
            status="N/A",
            notes="N/A ΓÇö not present in source (prodapi country-overview).",
        )

    present = not _is_refusal(answer)
    phrases = extra_phrases if extra_phrases is not None else _phrases_for_section(gt, section)
    cov, hit, miss = _coverage(answer, phrases)

    allowed_extra: list[str] = []
    if section == "cross_compare":
        allowed_extra = ["Saudi Arabia", "UAE", "Singapore"]
    elif section == "generic_compare":
        peer = _peer_for_generic(gt)
        if peer:
            allowed_extra = [peer]

    leaks = _leak_countries(answer, gt.name, allowed_extra=allowed_extra)

    notes_parts: list[str] = []
    if not present:
        notes_parts.append("Empty/refusal answer.")
    notes_parts.append(f"Coverage {cov:.0%} ({len(hit)}/{len(hit)+len(miss)} GT phrases).")
    if miss[:4]:
        notes_parts.append("Missing: " + "; ".join(m[:70] for m in miss[:4]))
    if leaks:
        notes_parts.append("COUNTRY MIX: leaked " + ", ".join(leaks))

    # Verdict thresholds
    if not present:
        status = "FAIL"
    elif leaks and section not in {"cross_compare", "generic_compare"}:
        status = "FAIL" if cov < 0.5 else "PARTIAL"
    elif cov >= 0.75 and present and (not leaks or section in {"cross_compare", "generic_compare"}):
        status = "PASS"
    elif cov >= 0.4:
        status = "PARTIAL"
    else:
        status = "FAIL"

    if section in {"cross_compare", "generic_compare"} and cov < 0.35:
        notes_parts.append(
            "Low coverage on compare metrics ΓÇö check retrieval boost for section=compare."
        )

    return CaseResult(
        country=gt.name,
        section=section,
        query=query,
        status=status,
        notes=" ".join(notes_parts),
        coverage=cov,
        present=present,
        leak_countries=leaks,
    )


def ask_agent(engine: QueryEngine, query: str) -> str:
    resp = engine.ask(query, source="website")
    return (resp.answer or "").strip()


def write_reports(results: list[CaseResult], *, prompt_review: str) -> None:
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    # CSV
    with REPORT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Country", "Section", "Query Used", "Pass/Partial/Fail", "Notes"])
        for r in results:
            w.writerow([r.country, r.section, r.query, r.status, r.notes])

    graded = [r for r in results if r.status != "N/A"]
    passes = sum(1 for r in graded if r.status == "PASS")
    partials = sum(1 for r in graded if r.status == "PARTIAL")
    fails = sum(1 for r in graded if r.status == "FAIL")
    nas = sum(1 for r in results if r.status == "N/A")
    rate = (passes / len(graded)) if graded else 0.0

    by_country: dict[str, dict[str, int]] = {}
    by_section: dict[str, dict[str, int]] = {}
    mixes: list[CaseResult] = []
    for r in results:
        if r.status == "N/A":
            continue
        by_country.setdefault(r.country, {"PASS": 0, "PARTIAL": 0, "FAIL": 0})
        by_country[r.country][r.status] += 1
        by_section.setdefault(r.section, {"PASS": 0, "PARTIAL": 0, "FAIL": 0})
        by_section[r.section][r.status] += 1
        if r.leak_countries:
            mixes.append(r)

    worst_countries = sorted(
        by_country.items(),
        key=lambda kv: (kv[1]["FAIL"] + 0.5 * kv[1]["PARTIAL"], -kv[1]["PASS"]),
        reverse=True,
    )
    worst_sections = sorted(
        by_section.items(),
        key=lambda kv: (kv[1]["FAIL"] + 0.5 * kv[1]["PARTIAL"], -kv[1]["PASS"]),
        reverse=True,
    )

    lines = [
        "# Country catalog agent eval",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Prompt review (adapted)",
        "",
        prompt_review.strip(),
        "",
        "## Summary",
        "",
        f"- Graded cases: **{len(graded)}** (PASS {passes}, PARTIAL {partials}, FAIL {fails})",
        f"- N/A skipped: **{nas}**",
        f"- Overall PASS rate (of graded): **{rate:.0%}**",
        "",
        "### Countries with most failures",
        "",
    ]
    for name, counts in worst_countries[:8]:
        lines.append(
            f"- **{name}**: FAIL {counts['FAIL']}, PARTIAL {counts['PARTIAL']}, PASS {counts['PASS']}"
        )
    lines += ["", "### Content areas with most failures", ""]
    for name, counts in worst_sections:
        lines.append(
            f"- **{name}**: FAIL {counts['FAIL']}, PARTIAL {counts['PARTIAL']}, PASS {counts['PASS']}"
        )
    lines += ["", "### Country-mix incidents (priority)", ""]
    if not mixes:
        lines.append("- None flagged.")
    else:
        for r in mixes:
            lines.append(
                f"- **{r.country} / {r.section}**: leaked {', '.join(r.leak_countries)} ΓÇö {r.notes}"
            )

    lines += ["", "## Full results", "", "| Country | Section | Status | Notes |", "|---|---|---|---|"]
    for r in results:
        note = r.notes.replace("|", "/")[:180]
        lines.append(f"| {r.country} | {r.section} | {r.status} | {note} |")

    lines += ["", f"CSV: `{REPORT_CSV.relative_to(ROOT).as_posix()}`", ""]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


PROMPT_REVIEW = """
**Agent invocation:** `QueryEngine.ask(question, source=\"website\")` (same stack as
`python -m app.cli query \"...\" --source website` and the portal on :8000).

**Ground truth:** live prodapi `GET /api/get-countries` + `/api/country-overview/:slug`
cached under `data/eval/country_ground_truth/*.json` (not HTML scrape files).
Package-detail GT is fetched live from `/micro-services/:type/:slug` or `/product-pages`.

**Prompt vs reality adjustments:**
1. **Content areas** mapped to prodapi: `marketInsights`, `keySellingPoints`,
   `overviewDetail`, `processStep`, `FAQs`, `compare` (cross + generic), `productPages`/`funds`,
   plus package detail (intro/highlights/process/FAQ/T&C/pricing).
2. **Locations** come from `prodapi-locations-ingest` (includes `compare` chunks).
3. **Package details** come from `prodapi-services-ingest` (micro-services + setup products).
4. Scoring is **phrase-coverage vs ground truth** (present / coverage / country-leak),
   not a second LLM judge ΓÇö comparable across countries, cheap to re-run.
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Eval country catalog agent answers")
    p.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    p.add_argument("--refresh-gt", action="store_true", help="Re-download prodapi GT")
    p.add_argument(
        "--limit-countries",
        type=int,
        default=None,
        help="Only first N countries (debug)",
    )
    p.add_argument(
        "--sections",
        default=None,
        help="Comma list of sections to run (default: all applicable)",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Pause between agent calls (seconds)",
    )
    return p.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    setup_logging()
    args = parse_args()

    section_filter = None
    if args.sections:
        section_filter = {s.strip() for s in args.sections.split(",") if s.strip()}

    client = ProdapiClient()
    print("Loading ground truth from prodapi / cacheΓÇª")
    gts = load_or_fetch_ground_truth(client, refresh=args.refresh_gt)
    if args.limit_countries:
        gts = gts[: args.limit_countries]
    print(f"Countries: {len(gts)}")

    settings = Settings(args.config)
    results: list[CaseResult] = []
    try:
        engine = QueryEngine(settings)
        # Cross-compare once (shared GT from Saudi if present, else UAE)
        cross_gt = next((g for g in gts if _norm(g.name) == "saudi arabia"), None)
        if cross_gt is None:
            cross_gt = next((g for g in gts if _norm(g.name) == "uae"), None)

        for gt in gts:
            sections = [
                "market_insights",
                "selling_points",
                "overview",
                "process_flow",
                "faq",
                "generic_compare",
                "packages",
            ]
            for section in sections:
                if section_filter and section not in section_filter:
                    continue
                if section == "generic_compare":
                    peer = _peer_for_generic(gt)
                    if not peer:
                        results.append(
                            CaseResult(
                                country=gt.name,
                                section=section,
                                query="",
                                status="N/A",
                                notes="N/A ΓÇö no comparison peers in source.",
                            )
                        )
                        continue
                    query = SECTION_QUERIES[section].format(country=gt.name, peer=peer)
                else:
                    query = SECTION_QUERIES[section].format(country=gt.name)

                if not gt.section_nonempty(section) and section != "generic_compare":
                    results.append(
                        CaseResult(
                            country=gt.name,
                            section=section,
                            query=query,
                            status="N/A",
                            notes="N/A ΓÇö not present in source (prodapi country-overview).",
                        )
                    )
                    continue

                print(f"-> {gt.name} / {section}")
                try:
                    answer = ask_agent(engine, query)
                except Exception as exc:
                    results.append(
                        CaseResult(
                            country=gt.name,
                            section=section,
                            query=query,
                            status="FAIL",
                            notes=f"Agent error: {exc}",
                        )
                    )
                    continue
                results.append(
                    score_case(gt=gt, section=section, query=query, answer=answer)
                )
                time.sleep(args.sleep)

            # Package detail ΓÇö first fund (micro-service) or setup product
            if not section_filter or "package_detail" in section_filter:
                try:
                    resolved = resolve_package_detail(client, gt)
                except Exception as exc:
                    results.append(
                        CaseResult(
                            country=gt.name,
                            section="package_detail",
                            query="",
                            status="FAIL",
                            notes=f"Detail GT fetch error: {exc}",
                        )
                    )
                    resolved = None
                if resolved is None:
                    results.append(
                        CaseResult(
                            country=gt.name,
                            section="package_detail",
                            query="",
                            status="N/A",
                            notes="N/A ΓÇö no fund/package detail available from prodapi for this country.",
                        )
                    )
                else:
                    title, phrases = resolved
                    query = SECTION_QUERIES["package_detail"].format(title=title)
                    print(f"-> {gt.name} / package_detail ({title[:48]})")
                    try:
                        answer = ask_agent(engine, query)
                        results.append(
                            score_case(
                                gt=gt,
                                section="package_detail",
                                query=query,
                                answer=answer,
                                extra_phrases=phrases,
                            )
                        )
                    except Exception as exc:
                        results.append(
                            CaseResult(
                                country=gt.name,
                                section="package_detail",
                                query=query,
                                status="FAIL",
                                notes=f"Agent error: {exc}",
                            )
                        )
                    time.sleep(args.sleep)

        if cross_gt and (not section_filter or "cross_compare" in section_filter):
            query = SECTION_QUERIES["cross_compare"]
            print("-> Cross-compare Saudi/UAE/Singapore")
            try:
                answer = ask_agent(engine, query)
                results.append(
                    score_case(
                        gt=cross_gt,
                        section="cross_compare",
                        query=query,
                        answer=answer,
                    )
                )
            except Exception as exc:
                results.append(
                    CaseResult(
                        country="Saudi Arabia / UAE / Singapore",
                        section="cross_compare",
                        query=query,
                        status="FAIL",
                        notes=f"Agent error: {exc}",
                    )
                )
    finally:
        settings.close()

    write_reports(results, prompt_review=PROMPT_REVIEW)
    print(f"\nWrote {REPORT_MD}")
    print(f"Wrote {REPORT_CSV}")
    graded = [r for r in results if r.status != "N/A"]
    passes = sum(1 for r in graded if r.status == "PASS")
    print(
        f"PASS {passes}/{len(graded)} graded "
        f"({(passes/len(graded) if graded else 0):.0%})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
