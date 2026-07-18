# Arnifi Enterprise RAG — Architecture

## Pipelines

```text
Offline ingest (CLI):
  scrape → extract → chunk → Bedrock Titan embed → Pinecone upsert

Online query (FastAPI / Lambda):
  question → Titan embed → Pinecone query → diversify → Nova Lite → SSE
```

## Package map (`app/`)

| Path | Responsibility |
|------|----------------|
| `app/api/` | FastAPI routes + Mangum Lambda handler |
| `app/services/bedrock/` | Bedrock client, Titan embeddings, Nova Lite LLM |
| `app/services/pinecone/` | Vector upsert / query |
| `app/services/retrieval/` | QueryEngine + diversify |
| `app/services/ingestion/` | Indexer, SQLite state, chunk/extract |
| `app/services/scraper/` | Fetcher + listing crawler |
| `app/services/prompting/` | System prompt loading |
| `app/config/` | Env + YAML settings / DI |
| `app/models/` | Pydantic schemas |
| `app/cli.py` | Crawl / ingest / query CLI |

`src/` keeps thin re-export shims for older import paths.

## AWS services

| Service | Why |
|---------|-----|
| **Amazon Bedrock** | Hosted Titan embeddings + Nova Lite chat (no self-hosted models) |
| **AWS Lambda** | Optional initial hosting for the FastAPI app |
| **IAM** | Least-privilege invoke for Bedrock |
| **Pinecone** | External vector DB (unchanged product choice) |

## Config

Secrets and model IDs come from environment variables (see `.env.example`).
Tunables (chunk size, retrieval caps, crawl seeds) live in `config/settings.yaml`.
