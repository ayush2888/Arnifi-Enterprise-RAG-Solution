"""
CLI entry point for the Arnifi blog RAG system.

Commands map to pipelines:
  crawl, ingest, index-posts, extract  →  Indexer (blogs)
  website-crawl, website-ingest        →  WebsiteCrawler / WebsiteIndexer
  query, inspect                       →  QueryEngine
  eval-rag                             →  Eval runner (hit@k / recall / answer checks)
  drive-list                           →  DriveClient (nested file listing)
  drive-discover                       →  folder/file/type summary for Drive
  drive-sync                           →  download + extract PDF/CSV/XLSX
  drive-ingest                         →  chunk + Titan + Pinecone
  drive-purge                          →  delete Drive vectors from Pinecone
  build-pricing-index                  →  export bundled PM/Title lexical JSON
  whatsapp-sync                        →  Periskope download groups/messages
  whatsapp-ingest                      →  episode summaries → Titan + Pinecone
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.env import load_env
from app.config.settings import Settings
from app.services.drive.client import DriveClient
from app.services.drive.discover import build_discover_report, format_discover_text
from app.services.ingestion.drive import DriveIndexer, DriveSync
from app.services.ingestion.pipeline import Indexer
from app.services.ingestion.website import WebsiteCrawler, WebsiteIndexer
from app.services.ingestion.whatsapp import WhatsAppIndexer, WhatsAppSync
from app.services.periskope.client import PeriskopeClient
from app.services.eval.runner import run_eval
from app.services.retrieval.engine import QueryEngine
from app.utils.helpers import setup_logging


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
    ingest_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed and upsert even if post content is unchanged (needed after embedding model/index switch)",
    )

    index_parser = sub.add_parser("index-posts", help="Fetch discovered posts, chunk, embed, upsert")
    index_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    index_parser.add_argument("--limit", type=int, default=None)
    index_parser.add_argument("--dry-run", action="store_true", help="Chunk without embedding or Pinecone upsert")
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed and upsert even if post content is unchanged",
    )

    query_parser = sub.add_parser("query", help="Ask a question against the vector index")
    query_parser.add_argument("question", help="User question")
    query_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    query_parser.add_argument(
        "--source",
        choices=["all", "website", "blog", "drive", "whatsapp"],
        default="all",
        help="Limit retrieval to website (includes blogs), drive, whatsapp, or all. blog is an alias of website.",
    )

    inspect_parser = sub.add_parser("inspect", help="Show crawl state and Pinecone stats")
    inspect_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))

    extract_parser = sub.add_parser("extract", help="Fetch and extract posts to JSON (no embeddings)")
    extract_parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    extract_parser.add_argument("--limit", type=int, default=3)
    extract_parser.add_argument("--url", default=None, help="Extract a specific post URL")

    website_crawl = sub.add_parser(
        "website-crawl",
        help="Discover allowlisted arnifi.com catalog URLs (does not touch blog post_urls)",
    )
    website_crawl.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))

    website_ingest = sub.add_parser(
        "website-ingest",
        help="Chunk catalog pages, embed with Titan, upsert source_type=website",
    )
    website_ingest.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    website_ingest.add_argument("--limit", type=int, default=None, help="Max catalog pages")
    website_ingest.add_argument("--dry-run", action="store_true", help="Chunk only; skip Pinecone")
    website_ingest.add_argument(
        "--force",
        action="store_true",
        help="Re-embed even if page content is unchanged",
    )

    drive_list = sub.add_parser(
        "drive-list",
        help="List nested Google Drive files under DRIVE_ROOT_FOLDER_ID",
    )
    drive_list.add_argument(
        "--folder-id",
        default=None,
        help="Override DRIVE_ROOT_FOLDER_ID from .env",
    )
    drive_list.add_argument(
        "--files-only",
        action="store_true",
        help="Hide folder rows; print files only",
    )
    drive_list.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Print at most N items",
    )

    drive_discover = sub.add_parser(
        "drive-discover",
        help="Summarize Drive folders/files by type and ingest support (no download)",
    )
    drive_discover.add_argument(
        "--folder-id",
        default=None,
        help="Override DRIVE_ROOT_FOLDER_ID from .env",
    )
    drive_discover.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON report instead of the compact text table",
    )
    drive_discover.add_argument(
        "--out",
        default=None,
        help="Optional path to write the JSON report (e.g. data/artifacts/drive/discover_report.json)",
    )

    drive_sync = sub.add_parser(
        "drive-sync",
        help="Download Drive PDF/CSV/XLSX files and extract text to data/artifacts/drive/",
    )
    drive_sync.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
        help="Path to settings.yaml (for artifacts_dir)",
    )
    drive_sync.add_argument(
        "--folder-id",
        default=None,
        help="Override DRIVE_ROOT_FOLDER_ID from .env",
    )
    drive_sync.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max files to download/extract (0 = unlimited, default)",
    )
    drive_sync.add_argument(
        "--path-contains",
        default=None,
        help="Only sync files whose Drive path contains this substring",
    )

    drive_ingest = sub.add_parser(
        "drive-ingest",
        help="Chunk extracted Drive files, embed with Titan, upsert to Pinecone",
    )
    drive_ingest.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    drive_ingest.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max extracted JSON files to ingest (0 = unlimited, default)",
    )
    drive_ingest.add_argument(
        "--path-contains",
        default=None,
        help="Only ingest extracted files whose full_path contains this substring",
    )
    drive_ingest.add_argument(
        "--purge-before-ingest",
        action="store_true",
        help="Delete matching Drive vectors in Pinecone before upsert",
    )
    drive_ingest.add_argument(
        "--skip-ids-file",
        default=None,
        help="Text file of Drive file_ids (one per line) already ingested; skip them",
    )
    drive_ingest.add_argument(
        "--only-ids-file",
        default=None,
        help="Text file of Drive file_ids to ingest exclusively (one per line)",
    )
    drive_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Chunk only; skip embedding and Pinecone upsert",
    )

    drive_purge = sub.add_parser(
        "drive-purge",
        help="Delete Drive vectors from Pinecone by metadata filter",
    )
    drive_purge.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    drive_purge.add_argument(
        "--all-drive",
        action="store_true",
        help="Delete all vectors with source_type=drive",
    )
    drive_purge.add_argument(
        "--source-url",
        action="append",
        default=[],
        help="Delete vectors for this Drive source_url (repeatable)",
    )

    pricing_index = sub.add_parser(
        "build-pricing-index",
        help=(
            "Export bundled PM#### / Title lexical index from Drive extracts "
            "(Lambda-safe; commit config/indexes/pricing_lexical.json)"
        ),
    )
    pricing_index.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    pricing_index.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: retrieval.pricing_lexical_index in settings)",
    )

    wa_sync = sub.add_parser(
        "whatsapp-sync",
        help="Download Periskope WhatsApp group chats/messages to data/artifacts/whatsapp/",
    )
    wa_sync.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    wa_sync.add_argument(
        "--limit-chats",
        type=int,
        default=None,
        help="Max groups to sync (default: all groups)",
    )

    wa_ingest = sub.add_parser(
        "whatsapp-ingest",
        help="Summarize WhatsApp episodes, embed with Titan, upsert to Pinecone",
    )
    wa_ingest.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    wa_ingest.add_argument(
        "--limit-chats",
        type=int,
        default=None,
        help="Max synced chat JSON files to ingest (default: all)",
    )
    wa_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Build episode chunks without Nova/Pinecone (placeholder summaries)",
    )
    wa_ingest.add_argument(
        "--force",
        action="store_true",
        help="Re-summarize even if episode content SHA1 is unchanged",
    )

    eval_rag = sub.add_parser(
        "eval-rag",
        help="Score RAG retrieval (+ optional generation) against data/eval/cases.jsonl",
    )
    eval_rag.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
    )
    eval_rag.add_argument(
        "--cases",
        default=str(ROOT / "data" / "eval" / "cases.jsonl"),
        help="Path to eval cases JSONL",
    )
    eval_rag.add_argument(
        "--out",
        default=str(ROOT / "data" / "eval" / "reports"),
        help="Directory for timestamped report folders",
    )
    eval_rag.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Limit number of cases (overrides eval.max_cases in settings)",
    )
    eval_rag.add_argument(
        "--tags",
        default=None,
        help="Comma-separated tag filter (e.g. smoke,pricing)",
    )
    eval_rag.add_argument(
        "--retrieve-only",
        action="store_true",
        help="Skip LLM generation; score hit@k / context_recall only",
    )
    eval_rag.add_argument(
        "--freeze-baseline",
        action="store_true",
        help="Copy this run's summary to data/eval/baselines/baseline_v1.json",
    )
    eval_rag.add_argument(
        "--baseline-path",
        default=str(ROOT / "data" / "eval" / "baselines" / "baseline_v1.json"),
        help="Target path when --freeze-baseline is set",
    )

    return parser


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _print_json(payload: object) -> None:
    """Print JSON safely on Windows consoles that reject some Unicode glyphs."""
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))


def _run_drive_list(folder_id: str | None, files_only: bool, limit: int | None) -> dict:
    env = load_env()
    sa_file = env.google_service_account_file
    root_id = folder_id or env.drive_root_folder_id
    if not sa_file:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_FILE in .env")
    if not root_id:
        raise RuntimeError("Set DRIVE_ROOT_FOLDER_ID in .env or pass --folder-id")

    client = DriveClient(_resolve_path(sa_file), root_id)
    root_name = client.get_folder_name()
    items = client.list_files(root_id) if files_only else client.list_tree(root_id)
    if limit is not None:
        items = items[:limit]

    rows = [
        {
            "path": item.full_path,
            "name": item.name,
            "type": "folder" if item.is_folder else "file",
            "mime_type": item.mime_type,
            "file_id": item.file_id,
            "modified_time": item.modified_time,
            "size": item.size,
        }
        for item in items
    ]
    file_count = sum(1 for r in rows if r["type"] == "file")
    folder_count = sum(1 for r in rows if r["type"] == "folder")
    return {
        "root_folder_id": root_id,
        "root_name": root_name,
        "total_items": len(rows),
        "files": file_count,
        "folders": folder_count,
        "items": rows,
    }


def _run_drive_discover(folder_id: str | None) -> dict:
    env = load_env()
    sa_file = env.google_service_account_file
    root_id = folder_id or env.drive_root_folder_id
    if not sa_file:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_FILE in .env")
    if not root_id:
        raise RuntimeError("Set DRIVE_ROOT_FOLDER_ID in .env or pass --folder-id")

    client = DriveClient(_resolve_path(sa_file), root_id)
    return build_discover_report(client, folder_id=root_id)


def _run_drive_sync(
    config_path: str,
    folder_id: str | None,
    limit: int,
    path_contains: str | None = None,
) -> dict:
    env = load_env()
    sa_file = env.google_service_account_file
    root_id = folder_id or env.drive_root_folder_id
    if not sa_file:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_FILE in .env")
    if not root_id:
        raise RuntimeError("Set DRIVE_ROOT_FOLDER_ID in .env or pass --folder-id")

    settings = Settings(config_path)
    try:
        artifacts_dir = settings.setting("paths", "artifacts_dir", default="data/artifacts")
        artifacts_path = _resolve_path(str(artifacts_dir))
        client = DriveClient(_resolve_path(sa_file), root_id)
        syncer = DriveSync(client, artifacts_path)
        return syncer.sync(
            limit=limit,
            folder_id=root_id,
            path_contains=path_contains,
        )
    finally:
        settings.close()


def _run_build_pricing_index(
    config_path: str,
    *,
    out_path: str | None,
) -> dict:
    from app.services.retrieval.service_code_lookup import (
        export_pricing_lexical_index,
        reset_service_code_index_cache,
    )

    settings = Settings(config_path)
    try:
        artifacts_dir = settings.artifacts.base_dir
        embedding_model = str(
            settings.setting(
                "embedding", "model", default="amazon.titan-embed-text-v2:0"
            )
        )
        if out_path:
            dest = _resolve_path(out_path)
        else:
            bundled = settings.setting(
                "retrieval",
                "pricing_lexical_index",
                default="config/indexes/pricing_lexical.json",
            )
            dest = Path(str(bundled))
            if not dest.is_absolute():
                dest = ROOT / dest
        result = export_pricing_lexical_index(
            artifacts_dir,
            dest,
            embedding_model=embedding_model,
        )
        reset_service_code_index_cache()
        return result
    finally:
        settings.close()


def _run_drive_purge(
    config_path: str,
    *,
    all_drive: bool,
    source_urls: list[str],
) -> dict:
    if not all_drive and not source_urls:
        raise RuntimeError("Pass --all-drive and/or one or more --source-url")

    settings = Settings(config_path)
    try:
        deleted = 0
        details: list[dict] = []
        if all_drive:
            deleted += settings.vectorstore.delete_by_filter(
                {"source_type": {"$eq": "drive"}}
            )
            details.append({"filter": "source_type=drive", "ok": True})
        for url in source_urls:
            deleted += settings.vectorstore.delete_by_filter(
                {
                    "source_type": {"$eq": "drive"},
                    "source_url": {"$eq": url},
                }
            )
            details.append({"source_url": url, "ok": True})
        stats = None
        try:
            stats = settings.vectorstore.describe_stats()
        except Exception as exc:
            stats = {"error": str(exc)}
        return {
            "deleted_requests": deleted,
            "details": details,
            "index_stats": stats,
        }
    finally:
        settings.close()


def _run_whatsapp_sync(config_path: str, limit_chats: int | None) -> dict:
    env = load_env()
    if not env.periskope_api_key or not env.periskope_phone:
        raise RuntimeError("Set PERISKOPE_API_KEY and PERISKOPE_PHONE in .env")

    settings = Settings(config_path)
    try:
        artifacts_dir = settings.setting("paths", "artifacts_dir", default="data/artifacts")
        artifacts_path = _resolve_path(str(artifacts_dir))
        delay = float(settings.setting("whatsapp", "request_delay_seconds", default=0.35))
        chat_type = str(settings.setting("whatsapp", "chat_type", default="group"))
        chat_page = int(settings.setting("whatsapp", "chat_page_size", default=100))
        msg_page = int(settings.setting("whatsapp", "message_page_size", default=100))
        allowlist = settings.setting("whatsapp", "chat_allowlist", default=[]) or []
        client = PeriskopeClient(
            env.periskope_api_key,
            env.periskope_phone,
            delay_seconds=delay,
        )
        syncer = WhatsAppSync(client, artifacts_path, settings.state_db)
        return syncer.sync(
            limit_chats=limit_chats,
            chat_type=chat_type,
            chat_page_size=chat_page,
            message_page_size=msg_page,
            chat_allowlist=list(allowlist),
        )
    finally:
        settings.close()


def main() -> None:
    load_dotenv(ROOT / ".env")
    setup_logging()
    # Windows consoles (cp1252) choke on some PDF preview unicode in JSON dumps.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "drive-list":
        result = _run_drive_list(
            folder_id=args.folder_id,
            files_only=args.files_only,
            limit=args.limit,
        )
        _print_json(result)
        return

    if args.command == "drive-discover":
        result = _run_drive_discover(folder_id=args.folder_id)
        if args.out:
            out_path = _resolve_path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            print(f"Wrote JSON report: {out_path}", file=sys.stderr)
        if args.json:
            _print_json(result)
        else:
            print(format_discover_text(result))
        return

    if args.command == "drive-sync":
        result = _run_drive_sync(
            config_path=args.config,
            folder_id=args.folder_id,
            limit=args.limit,
            path_contains=args.path_contains,
        )
        _print_json(result)
        return

    if args.command == "drive-purge":
        result = _run_drive_purge(
            config_path=args.config,
            all_drive=args.all_drive,
            source_urls=list(args.source_url or []),
        )
        _print_json(result)
        return

    if args.command == "build-pricing-index":
        result = _run_build_pricing_index(
            config_path=args.config,
            out_path=args.out,
        )
        _print_json(result)
        return

    if args.command == "whatsapp-sync":
        result = _run_whatsapp_sync(
            config_path=args.config,
            limit_chats=args.limit_chats,
        )
        _print_json(result)
        return

    if args.command == "website-crawl":
        settings = Settings(args.config)
        try:
            result = WebsiteCrawler(settings).crawl()
            _print_json(result)
        finally:
            settings.close()
        return

    if args.command == "website-ingest":
        settings = Settings(args.config)
        try:
            result = WebsiteIndexer(settings).ingest(
                limit=args.limit,
                skip_unchanged=not args.force,
                dry_run=args.dry_run,
            )
            _print_json(result)
        finally:
            settings.close()
        return

    if args.command == "eval-rag":
        settings = Settings(args.config)
        try:
            max_cases = args.max_cases
            if max_cases is None:
                cfg_max = settings.setting("eval", "max_cases", default=None)
                max_cases = int(cfg_max) if cfg_max is not None else None
            tags = None
            if args.tags:
                tags = [t.strip() for t in args.tags.split(",") if t.strip()]
            if args.retrieve_only:
                generate = False
            else:
                generate = bool(settings.setting("eval", "generate", default=True))
            freeze_path = (
                _resolve_path(args.baseline_path) if args.freeze_baseline else None
            )
            result = run_eval(
                QueryEngine(settings),
                cases_path=_resolve_path(args.cases),
                out_dir=_resolve_path(args.out),
                max_cases=max_cases,
                tags=tags,
                generate=generate,
                freeze_baseline_path=freeze_path,
            )
            _print_json(
                {
                    "report_dir": result["report_dir"],
                    "baseline_path": result.get("baseline_path"),
                    "means": result["summary"]["means"],
                    "by_tag": result["summary"]["by_tag"],
                    "n_cases": result["summary"]["n_cases"],
                    "run_id": result["summary"]["run_id"],
                }
            )
        finally:
            settings.close()
        return

    settings = Settings(args.config)
    indexer = Indexer(settings)
    querier = QueryEngine(settings)

    try:
        if args.command == "whatsapp-ingest":
            result = WhatsAppIndexer(settings).ingest(
                limit_chats=args.limit_chats,
                dry_run=args.dry_run,
                force=args.force,
            )
        elif args.command == "drive-ingest":
            skip_ids: set[str] = set()
            only_ids: set[str] = set()
            if args.skip_ids_file:
                skip_path = _resolve_path(args.skip_ids_file)
                skip_ids = {
                    line.strip()
                    for line in skip_path.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                }
            if getattr(args, "only_ids_file", None):
                only_path = _resolve_path(args.only_ids_file)
                only_ids = {
                    line.strip()
                    for line in only_path.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                }
            result = DriveIndexer(settings).ingest(
                limit=args.limit,
                dry_run=args.dry_run,
                path_contains=args.path_contains,
                purge_before_ingest=args.purge_before_ingest,
                skip_file_ids=skip_ids,
                only_file_ids=only_ids,
            )
        elif args.command == "crawl":
            result = indexer.crawl_listings(max_pages_per_listing=args.max_pages)
        elif args.command == "ingest":
            skip_unchanged = not args.force
            if args.posts_only:
                result = indexer.index_posts(
                    limit=args.limit,
                    skip_unchanged=skip_unchanged,
                )
            else:
                result = indexer.run_full_index(
                    post_limit=args.limit,
                    skip_unchanged=skip_unchanged,
                )
        elif args.command == "index-posts":
            result = indexer.index_posts(
                limit=args.limit,
                dry_run=args.dry_run,
                skip_unchanged=not args.force,
            )
        elif args.command == "query":
            response = querier.ask(args.question, source=args.source)
            _print_json(response.model_dump())
            return
        elif args.command == "inspect":
            result = querier.inspect()
        elif args.command == "extract":
            result = indexer.extract_posts(limit=args.limit, url=args.url)
        else:
            parser.error(f"Unknown command: {args.command}")
            return

        _print_json(result)
    finally:
        settings.close()


if __name__ == "__main__":
    main()
