"""Sample answer-generation report for website catalog retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import Settings
from app.services.retrieval.engine import QueryEngine
from app.utils.helpers import setup_logging

QUESTIONS = [
    ("Q1_ifza_cost", "from the website, what is IFZA visa cost?", "website"),
    ("Q2_dmcc_case", "Have we published a case study on logistics companies scaling in DMCC?", "website"),
    ("Q3_dubai_office", "What is Arnifi's Dubai office address?", "website"),
    ("Q4_shams_package", "What is the starting price of the SHAMS standard package 1 visa 1 year?", "all"),
]


def main() -> None:
    load_dotenv(ROOT / ".env")
    setup_logging()
    settings = Settings(ROOT / "config" / "settings.yaml")
    querier = QueryEngine(settings)
    rows = []
    try:
        for qid, question, source in QUESTIONS:
            print(f"\n===== {qid} source={source} =====", flush=True)
            print(question, flush=True)
            resp = querier.ask(question, source=source)
            sources = []
            for item in resp.sources:
                url = item.get("source_url") or ""
                sources.append(
                    {
                        "title": item.get("doc_title"),
                        "url": url,
                        "heading": item.get("heading_path"),
                        "source_type": item.get("source_type"),
                        "family": (
                            "catalog"
                            if any(
                                p in url
                                for p in (
                                    "/services/",
                                    "/product-details/",
                                    "/pricing-master-list",
                                    "/case-studies/",
                                    "/country-overview/",
                                    "llms.txt",
                                    "/contact-us",
                                )
                            )
                            else (
                                "drive"
                                if "drive.google.com" in url
                                else "blog"
                                if "/blog/" in url
                                else "other"
                            )
                        ),
                    }
                )
            row = {
                "id": qid,
                "question": question,
                "source_filter": source,
                "answer": resp.answer,
                "sources": sources,
            }
            rows.append(row)
            print(resp.answer[:1200], flush=True)
            for src in sources:
                print(f"  - [{src['family']}] {src['url']}", flush=True)
    finally:
        settings.close()

    out = ROOT / "data" / "artifacts" / "website" / "sample_answer_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)


if __name__ == "__main__":
    main()
