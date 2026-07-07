"""
CLI entry point for the Arnifi blog RAG system.

Commands map to pipelines:
  crawl, ingest, index-posts, extract  →  Indexer (src/ingest.py)
  query, inspect                       →  QueryEngine (src/query.py)
"""

from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings
from src.ingest import Indexer
from src.query import QueryEngine
from src.utils import setup_logging


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Arnifi Multi-Page Global Blog RAG")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl_parser = sub.add_parser("crawl", help="Crawl listing/category pages and discover post URLs")
    crawl_parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
        help="Path to settings.yaml",
    )
    crawl_parser.add_argument("--max-pages", type=int, default=None, help="Override max pages per listing")

    ingest_parser = sub.add_parser("ingest", help="Crawl listings and ingest posts into Pinecone")
    ingest_parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    ingest_parser.add_argument("--limit", type=int, default=None, help="Limit number of posts to ingest")
    ingest_parser.add_argument("--posts-only", action="store_true", help="Skip listing crawl")

    index_parser = sub.add_parser("index-posts", help="Fetch discovered posts, chunk, embed, upsert")
    index_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    index_parser.add_argument("--limit", type=int, default=None)
    index_parser.add_argument("--dry-run", action="store_true", help="Chunk without embedding or Pinecone upsert")

    query_parser = sub.add_parser("query", help="Ask a question against the vector index")
    query_parser.add_argument("question", help="User question")
    query_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))

    inspect_parser = sub.add_parser("inspect", help="Show crawl state and Pinecone stats")
    inspect_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))

    extract_parser = sub.add_parser("extract", help="Fetch and extract posts to JSON (no embeddings)")
    extract_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    extract_parser.add_argument("--limit", type=int, default=3)
    extract_parser.add_argument("--url", default=None, help="Extract a specific post URL")

    return parser


def main() -> None:
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY") and os.getenv("OPEN_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["OPEN_API_KEY"]
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    settings = Settings(args.config)
    indexer = Indexer(settings)
    querier = QueryEngine(settings)

    try:
        if args.command == "crawl":
            result = indexer.crawl_listings(max_pages_per_listing=args.max_pages)
        elif args.command == "ingest":
            if args.posts_only:
                result = indexer.index_posts(limit=args.limit)
            else:
                result = indexer.run_full_index(post_limit=args.limit)
        elif args.command == "index-posts":
            result = indexer.index_posts(limit=args.limit, dry_run=args.dry_run)
        elif args.command == "query":
            response = querier.ask(args.question)
            print(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))
            return
        elif args.command == "inspect":
            result = querier.inspect()
        elif args.command == "extract":
            result = indexer.extract_posts(limit=args.limit, url=args.url)
        else:
            parser.error(f"Unknown command: {args.command}")
            return

        print(json.dumps(result, indent=2, default=str))
    finally:
        settings.close()


if __name__ == "__main__":
    main()
