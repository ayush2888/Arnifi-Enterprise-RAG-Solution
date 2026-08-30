# Arnifi Enterprise RAG

A production RAG system that answers questions using Arnifi's knowledge — from the public blog and from WhatsApp conversations synced via Periskope.

Users ask in natural language. The system embeds the question, searches a shared Pinecone index, and returns a grounded answer from **Amazon Nova Lite** with citations. Answers are built from indexed content only, not model memory.

---

## Knowledge sources

| Source | How it enters the system |
|--------|--------------------------|
| **Blog** | Crawls `arnifi.com/blog` listing pages, extracts posts, chunks by section |
| **WhatsApp** | Pulls chats and messages from Periskope, groups them into episodes, summarizes each episode with Nova Lite |

Both sources use the same embedding model, the same Pinecone index, and the same query engine. Chunks are tagged with `source_type` (`blog` or `whatsapp`) for filtering and citations.

---

## How it works

### End-to-end flow

```text
┌─────────────────────────────────────────────────────────────────┐
│                     Arnifi Knowledge Sources                    │
├────────────────────────────┬────────────────────────────────────┤
│  Blog (arnifi.com)         │  WhatsApp (Periskope API)          │
└─────────────┬──────────────┴──────────────────┬─────────────────┘
              │                                  │
              ▼                                  ▼
     Crawl → Parse → Chunk              Fetch → Normalize → Episodes
              │                                  │
              │                                  ▼
              │                         LLM Episode Summaries
              │                                  │
              └──────────────┬───────────────────┘
                             ▼
                  Titan Embeddings (1024-d)
                             ▼
              Pinecone (single index, source_type metadata)
                             ▼
              Query → Diversify → Nova Lite → Answer + Sources
```

### Blog ingestion

```text
Listing pages → discover URLs → fetch HTML → extract content
  → chunk (~400 tokens) → Titan embed → Pinecone upsert
```

### WhatsApp ingestion

```text
Periskope API → list chats → download messages → cache raw JSON
  → normalize messages → build episodes (time-gap rules)
  → Nova Lite summarizes each episode → Titan embed → Pinecone upsert
```

### Query

```text
Question → Titan embed → Pinecone search → diversify results
  → Nova Lite + context → streamed answer + sources
```

Retrieval searches **all** knowledge by default. You can restrict to `blog`, `whatsapp`, or `all` via CLI or API.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ |
| API | FastAPI + Server-Sent Events (SSE) |
| Embeddings | Amazon Bedrock — Titan Embed Text V2 (1024-d) |
| LLM | Amazon Bedrock — Amazon Nova Lite |
| Vector DB | Pinecone (cosine, bring-your-own vectors) |
| WhatsApp sync | Periskope REST API |
| Crawl state | SQLite |
| Artifacts | Local disk (`data/artifacts/`) |
| Deploy | Docker + AWS Lambda (optional) |

---

## Project structure

```text
Arnifi-Enterprise-RAG-Solution/
├── app/
│   ├── cli.py                          # All CLI commands
│   ├── api/
│   │   ├── server.py                   # FastAPI portal + SSE chat
│   │   └── handler.py                  # AWS Lambda entry (Mangum)
│   ├── config/
│   │   ├── settings.py                 # YAML + env service wiring
│   │   └── env.py                      # Secrets from environment
│   ├── models/
│   │   └── schemas.py                  # Document, Chunk, RAGResponse
│   └── services/
│       ├── bedrock/                    # Titan embeddings + Nova Lite
│       ├── pinecone/                   # Vector upsert / query / filter
│       ├── periskope/                  # Periskope HTTP client
│       ├── ingestion/
│       │   ├── pipeline.py             # Blog Indexer
│       │   └── whatsapp.py             # WhatsApp Indexer
│       ├── retrieval/                  # QueryEngine + diversify
│       └── prompting/                  # Prompt templates
├── config/
│   ├── settings.yaml                   # Tunables (seeds, chunking, episodes)
│   └── prompts/
│       ├── answer_synthesis.md         # Answer generation prompt
│       └── episode_summary.md          # WhatsApp episode summarization
├── frontend/                           # Static chat UI
├── data/                               # SQLite state + cached artifacts
├── deploy/                             # Lambda deploy scripts
├── tests/                              # Unit tests
└── docs/ARCHITECTURE.md
```

`src/` holds legacy import shims. All application code lives under `app/`.

---

## Quick start

### 1. Install

```powershell
cd Arnifi-Enterprise-RAG-Solution
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

### 2. Configure `.env`

| Variable | Purpose |
|----------|---------|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS credentials (local dev) |
| `AWS_REGION` | Default AWS region |
| `BEDROCK_REGION` | Region where Bedrock models are enabled |
| `BEDROCK_EMBED_MODEL` | `amazon.titan-embed-text-v2:0` |
| `BEDROCK_CHAT_MODEL` | `amazon.nova-lite-v1:0` |
| `PINECONE_API_KEY` | Pinecone API key |
| `PINECONE_INDEX` | Index name (e.g. `arnifi-rag-titan`) |
| `PINECONE_ENVIRONMENT` | Pinecone region |
| `PERISKOPE_API_KEY` | Periskope API key |
| `PERISKOPE_PHONE` | Org phone for `x-phone` header (digits only) |

### 3. Prerequisites

- Enable **Titan Embed Text V2** and **Nova Lite** in the Bedrock console for `BEDROCK_REGION`
- Create a Pinecone index: **1024 dimensions**, **cosine**, **no hosted embedding model**
- Generate a Periskope API key under Settings → Integrations → API

### 4. Index knowledge

**Blog:**

```powershell
python -m app.cli crawl
python -m app.cli ingest --limit 10
```

**Website catalog (locations + services):** see [docs/WEBSITE_INGEST_2026-08-29.md](docs/WEBSITE_INGEST_2026-08-29.md).

```powershell
python -m app.cli prodapi-locations-ingest --dry-run
python -m app.cli prodapi-locations-ingest --force
python -m app.cli prodapi-services-ingest --dry-run
python -m app.cli prodapi-services-ingest
python -m app.cli website-crawl
python -m app.cli website-ingest --page-kind country_overview --dry-run
python -m app.cli website-ingest --page-kind country_overview
```

**WhatsApp:**

```powershell
python -m app.cli whatsapp-sync
python -m app.cli whatsapp-ingest --limit-chats 5
```

**Check status:**

```powershell
python -m app.cli inspect
```

### 5. Ask questions

**CLI:**

```powershell
python -m app.cli query "What is the VAT refund process in Malaysia?"
python -m app.cli query "How do we handle Cayman company setup?" --source all
python -m app.cli query "What did the team say about visa timelines?" --source whatsapp
```

**Web portal:**

```powershell
.\scripts\run_portal.ps1
# Open http://127.0.0.1:8000
```

---

## CLI reference

### Blog commands

| Command | Description |
|---------|-------------|
| `crawl` | Discover blog post URLs from listing pages |
| `ingest` | Full pipeline: crawl listings + index posts |
| `index-posts` | Index already-discovered posts |
| `extract` | Parse posts to JSON without embedding |
| `inspect` | Show crawl state and Pinecone stats |
| `query` | Ask a question against the index |

### Website catalog commands

| Command | Description |
|---------|-------------|
| `prodapi-locations-ingest` | Prefer for locations: prodapi country-overview (incl. `compare`) ΓåÆ Pinecone |
| `prodapi-services-ingest` | Micro-service package details + country setup products ΓåÆ Pinecone |
| `website-crawl` | Discover homepage/footer, country, service, and SKU URLs |
| `website-ingest` | Extract sections, embed, upsert `source_type=website` |

```powershell
python -m app.cli prodapi-locations-ingest --dry-run
python -m app.cli prodapi-locations-ingest --force
python -m app.cli prodapi-services-ingest --dry-run
python -m app.cli prodapi-services-ingest
python -m app.cli website-ingest --page-kind service_landing --dry-run
python -m app.cli website-ingest --page-kind service_package
python -m app.cli website-ingest --force
```

`prodapi-locations-ingest` loads all navbar countries (or `--slug` / `--limit`). Includes the cross-country `compare` table. Unchanged overviews are skipped unless `--force`. Before upsert it deletes existing vectors for that country `source_url` so HTML and API ingest do not duplicate.

`prodapi-services-ingest` loads every micro-service detail (`/micro-services/:type/:slug`) plus setup/licence products referenced on country pages (`/product-pages?filters[id][$eq]=ΓÇª`). Use `--service-type Funds` to slice, `--no-setup-products` to skip licences, `--force` to re-embed.

`--page-kind` limits HTML ingest to `country_overview`, `service_landing`, `service_package`, or `product_detail`. Unchanged pages are skipped via content hash; 404 pages are purged from Pinecone unless `--no-prune-gone`. Recrawl weekly (or when Accounting/Visa pages change) with `website-crawl` then `website-ingest`.

### WhatsApp commands

| Command | Description |
|---------|-------------|
| `whatsapp-sync` | List chats and download messages from Periskope |
| `whatsapp-ingest` | Build episodes, summarize, embed, and upsert |

### Useful flags

```powershell
python -m app.cli ingest --limit 10 --force
python -m app.cli index-posts --dry-run
python -m app.cli whatsapp-ingest --limit-chats 3 --force
python -m app.cli query "..." --source blog|whatsapp|all
```

| Flag | Effect |
|------|--------|
| `--limit` / `--limit-chats` | Cap items processed |
| `--force` | Re-process unchanged content |
| `--dry-run` | Chunk/summarize only; skip Pinecone upsert |
| `--source` | Filter retrieval to `blog`, `whatsapp`, or `all` |

---

## Retrieval

1. Question is embedded with Titan (same model used at index time)
2. Pinecone returns the top similar chunks (optionally filtered by `source_type`)
3. **Diversify** keeps at most 2 chunks per source URL and returns up to 5 total
4. Nova Lite receives the question, retrieved context, and the system prompt
5. The answer cites sources — blog posts by title/URL/section; WhatsApp by chat name and episode date range

---

## Configuration

**Secrets** live in `.env` (see `.env.example`).

**Tunables** live in `config/settings.yaml`:

| Section | Controls |
|---------|----------|
| `seeds` | Blog listing URLs to crawl |
| `crawl` | Domains, politeness delay, retries |
| `chunk` | Max tokens and overlap for blog chunks |
| `whatsapp` | Episode gap minutes, min/max messages per episode, chat allowlist |
| `retrieval` | Top-k, max chunks returned, max per source URL |
| `embedding` / `llm` | Model defaults (overridden by env) |
| `pinecone` | Index name, namespace, metric |

---

## Deployment

**Local** is the default for ingestion (blog crawl and WhatsApp sync).

**AWS Lambda** hosts the query API:

- See [deploy/README.md](deploy/README.md)
- Build with `Dockerfile.lambda`
- Set Bedrock, Pinecone, and Periskope env vars on the function
- Set an AWS Budget alert before exposing a public Function URL

---

## Data and privacy

- `.env` and `data/` are not committed to git
- Raw WhatsApp messages are cached locally under `data/artifacts/whatsapp/`
- Only **episode summaries** are embedded and stored in Pinecone — not raw chat logs
- Summarization prompts exclude personal identifiers (names, phone numbers)
- Blog HTML and extracted JSON are cached under `data/artifacts/` for debugging and incremental re-indexing

---

## Design principles

- **One embedding path, one index, one query engine** — no duplicate utilities
- **Grounded answers** — the LLM only sees retrieved context
- **Incremental indexing** — SQLite tracks content hashes; unchanged items are skipped
- **Simple modules** — small functions, minimal abstraction, descriptive names
- **Provider-agnostic interfaces** — `Embedder`, `VectorStore`, and `LLM` protocols allow swapping backends without rewriting RAG logic

---

## Testing

```powershell
pytest tests/
```

Covers chunking, extraction, episode building, embedding helpers, retrieval, and URL classification.

---

## Documentation

- [Architecture overview](docs/ARCHITECTURE.md)
- [Website catalog ingest (29 Aug 2026)](docs/WEBSITE_INGEST_2026-08-29.md)
- [Lambda deployment](deploy/README.md)
- [Periskope API docs](https://docs.periskope.app/)

---

## Important notes

1. **Re-ingest after model changes** — vectors from a different embedding model are incompatible. Use `--force` after switching models.
2. **Same index for both sources** — blog and WhatsApp chunks coexist in one Pinecone index, separated by `source_type` metadata.
3. **Ingestion is offline** — crawl and WhatsApp sync run via CLI on a machine with disk access. Lambda serves queries only.

---

## License

Internal Arnifi project. Contact the team for usage and deployment permissions.
